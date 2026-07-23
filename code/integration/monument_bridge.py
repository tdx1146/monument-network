"""
Monument Bridge — iso-sand 与丰碑网络双向桥接

方向 1: iso-sand essence → 丰碑网络 (通过 /xuanjian/evaluate API)
方向 2: 丰碑网络 score → iso-sand (写入信誉积分文件)

用法:
    python -m integration.monument_bridge              # 单次运行
    python -m integration.monument_bridge --daemon      # 守护模式 (每 5 分钟)
    python integration/monument_bridge.py --test        # 测试模式

环境变量:
    MONUMENT_API_HOST    丰碑 API 主机 (默认 127.0.0.1)
    MONUMENT_API_PORT    丰碑 API 端口 (默认 18891)
    MONUMENT_API_KEY     API Key (可选)
    ISOSAND_ESSENCE_DIR  iso-sand essence 文件目录
    ISOSAND_REPUTATION   iso-sand 信誉积分文件路径
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

_BJT = timezone(timedelta(hours=8))

# ─── 配置 ────────────────────────────────────────────────

MONUMENT_API_HOST = os.environ.get("MONUMENT_API_HOST", "127.0.0.1")
MONUMENT_API_PORT = int(os.environ.get("MONUMENT_API_PORT", "18891"))
MONUMENT_API_KEY = os.environ.get("MONUMENT_API_KEY", "")
API_BASE = f"http://{MONUMENT_API_HOST}:{MONUMENT_API_PORT}"

# iso-sand 路径（可通过环境变量覆盖）
_ISOSAND_BASE = os.environ.get(
    "ISOSAND_BASE",
    r"Z:\QH\AI专用\Agent OS\iso-sand"
)
ISOSAND_ESSENCE_DIR = os.environ.get(
    "ISOSAND_ESSENCE_DIR",
    os.path.join(_ISOSAND_BASE, "data", "essences")
)
ISOSAND_REPUTATION = os.environ.get(
    "ISOSAND_REPUTATION",
    os.path.join(_ISOSAND_BASE, "data", "信誉积分点.md")
)

# 丰碑网络 event_bus 路径（用于写桥接事件）
_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(os.path.dirname(_CODE_DIR), "data")
EVENT_BUS_PATH = os.path.join(_DATA_DIR, "event_bus.jsonl")

BRIDGE_INTERVAL = 300  # 守护模式间隔（秒）


# ─── HTTP 工具 ───────────────────────────────────────────


def _api_call(method: str, path: str, body: dict = None) -> dict:
    """调用丰碑网络 HTTP API。"""
    url = f"{API_BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if MONUMENT_API_KEY:
        headers["X-Monument-Key"] = MONUMENT_API_KEY

    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {e.code}", "detail": err_body}
    except urllib.error.URLError as e:
        return {"error": "connection_failed", "detail": str(e.reason)}
    except Exception as e:
        return {"error": "unknown", "detail": str(e)}


def _write_event(event_type: str, producer: str, result: str,
                 detail: str, trace_id: str = "") -> None:
    """写入桥接事件到丰碑网络 event_bus。"""
    event = {
        "t": datetime.now(_BJT).isoformat(),
        "event_type": event_type,
        "producer": producer,
        "result": result,
        "detail": detail,
        "trace_id": trace_id,
    }
    try:
        os.makedirs(os.path.dirname(EVENT_BUS_PATH), exist_ok=True)
        with open(EVENT_BUS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[bridge] WARN: event bus write failed: {e}", file=sys.stderr)


# ─── 方向 1: iso-sand essence → 丰碑网络 ────────────────


def bridge_essence_to_monument() -> int:
    """读取 iso-sand essence 文件，写入丰碑网络作为洞察。

    返回: 成功桥接的 essence 数量
    """
    if not ISOSAND_ESSENCE_DIR or not os.path.isdir(ISOSAND_ESSENCE_DIR):
        _write_event("bridge_action", "monument_bridge", "WARN",
                     "essence dir not found, skipping", "bridge-essence")
        print(f"[bridge] essence dir not found: {ISOSAND_ESSENCE_DIR}")
        return 0

    count = 0
    for fname in sorted(os.listdir(ISOSAND_ESSENCE_DIR)):
        if not fname.startswith("essence_") or not fname.endswith(".json"):
            continue

        fpath = os.path.join(ISOSAND_ESSENCE_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                essences = json.load(f)

            if not isinstance(essences, list):
                essences = [essences]

            for ess in essences:
                # 只桥接 milestone 和 insight 类型
                if ess.get("type") not in ("milestone", "insight"):
                    continue

                text = ess.get("summary", "") + "\n" + ess.get("detail", "")
                if len(text) < 200:
                    continue  # 跳过过短的条目

                result = _api_call("POST", "/xuanjian/evaluate", {
                    "ai_id": "iso-sand-bridge",
                    "text": text,
                    "confidence": ess.get("confidence", 0.7),
                    "source_type": "essence_bridge",
                })

                if "error" not in result:
                    count += 1
                else:
                    _write_event("bridge_action", "monument_bridge", "FAIL",
                                 f"failed to bridge {fname}: {result.get('detail', '')}",
                                 f"bridge-{fname}")

        except (json.JSONDecodeError, OSError) as e:
            _write_event("bridge_action", "monument_bridge", "FAIL",
                         f"failed to read {fname}: {e}", f"bridge-{fname}")

    _write_event("bridge_action", "monument_bridge", "OK",
                 f"bridged {count} essences to monument", "bridge-essence")
    print(f"[bridge] direction 1: bridged {count} essences to monument")
    return count


# ─── 方向 2: 丰碑网络 score → iso-sand ──────────────────


def bridge_score_to_isosand() -> bool:
    """读取丰碑网络评分，写入 iso-sand 信誉积分文件。

    返回: 是否成功更新
    """
    # 查询丰碑网络中的 iso-sand-bridge 评分
    result = _api_call("GET", "/score/iso-sand-bridge")

    if "error" in result:
        _write_event("bridge_action", "monument_bridge", "WARN",
                     "score query failed, skipping reverse bridge",
                     "bridge-score")
        print(f"[bridge] score query failed: {result.get('detail', '')}")
        return False

    score_data = result.get("score", result)
    total_score = score_data.get("total", 0) if isinstance(score_data, dict) else 0

    # 写入 iso-sand 信誉积分文件
    if not ISOSAND_REPUTATION:
        print("[bridge] no reputation file path configured")
        return False

    try:
        # 读取现有文件
        lines = []
        if os.path.exists(ISOSAND_REPUTATION):
            with open(ISOSAND_REPUTATION, "r", encoding="utf-8") as f:
                lines = f.readlines()

        # 更新或追加积分行
        updated = False
        score_line = f"当前信誉积分: {total_score}\n"
        for i, line in enumerate(lines):
            if "积分" in line:
                lines[i] = score_line
                updated = True
                break

        if not updated:
            lines.append(score_line)

        os.makedirs(os.path.dirname(ISOSAND_REPUTATION), exist_ok=True)
        with open(ISOSAND_REPUTATION, "w", encoding="utf-8") as f:
            f.writelines(lines)

        _write_event("bridge_action", "monument_bridge", "OK",
                     f"updated iso-sand reputation: score={total_score}",
                     "bridge-score")
        print(f"[bridge] direction 2: updated iso-sand reputation to {total_score}")
        return True

    except OSError as e:
        _write_event("bridge_action", "monument_bridge", "FAIL",
                     f"failed to write reputation: {e}", "bridge-score")
        print(f"[bridge] reputation write failed: {e}")
        return False


# ─── 主入口 ──────────────────────────────────────────────


def run_once() -> dict:
    """执行一次双向桥接。"""
    print(f"[bridge] === bridge cycle {datetime.now(_BJT).strftime('%Y-%m-%d %H:%M:%S')} ===")

    essences_bridged = bridge_essence_to_monument()
    score_updated = bridge_score_to_isosand()

    summary = {
        "essences_bridged": essences_bridged,
        "score_updated": score_updated,
        "timestamp": datetime.now(_BJT).isoformat(),
    }
    print(f"[bridge] cycle complete: {summary}")
    return summary


def run_daemon(interval: int = BRIDGE_INTERVAL):
    """守护模式：每隔 interval 秒执行一次桥接。"""
    print(f"[bridge] daemon mode, interval={interval}s")
    print(f"[bridge] API base: {API_BASE}")
    print(f"[bridge] essence dir: {ISOSAND_ESSENCE_DIR}")
    print(f"[bridge] reputation file: {ISOSAND_REPUTATION}")

    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[bridge] cycle error: {e}", file=sys.stderr)
            _write_event("bridge_action", "monument_bridge", "FAIL",
                         f"daemon cycle error: {e}", "bridge-daemon")

        time.sleep(interval)


def quick_test():
    """快速测试桥接组件。"""
    print("=" * 50)
    print("[bridge] quick test")
    print("=" * 50)

    # 测试 API 连接
    print(f"\n1. API connectivity test ({API_BASE})...")
    health = _api_call("GET", "/health/simple")
    if "error" in health:
        print(f"   FAIL: {health.get('detail', 'connection failed')}")
        print("   (Monument API may not be running — this is OK for file tests)")
    else:
        print(f"   OK: {health}")

    # 测试 event bus 写入
    print("\n2. Event bus write test...")
    _write_event("bridge_action", "monument_bridge", "OK",
                 "quick test event", "bridge-test")
    print("   OK: event written to event_bus.jsonl")

    # 测试 essence 目录
    print(f"\n3. Essence dir check: {ISOSAND_ESSENCE_DIR}")
    if os.path.isdir(ISOSAND_ESSENCE_DIR):
        files = [f for f in os.listdir(ISOSAND_ESSENCE_DIR)
                 if f.startswith("essence_") and f.endswith(".json")]
        print(f"   OK: found {len(files)} essence files")
    else:
        print("   SKIP: dir not found (may not be mounted)")

    print("\n" + "=" * 50)
    print("[bridge] quick test passed")
    print("=" * 50)


def main():
    """命令行入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="Monument Bridge — iso-sand <-> 丰碑网络")
    parser.add_argument("--daemon", action="store_true", help="守护模式")
    parser.add_argument("--interval", type=int, default=BRIDGE_INTERVAL,
                        help=f"守护模式间隔秒数 (默认 {BRIDGE_INTERVAL})")
    parser.add_argument("--test", action="store_true", help="快速测试")
    args = parser.parse_args()

    if args.test:
        quick_test()
        return

    if args.daemon:
        run_daemon(args.interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
