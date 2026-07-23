"""
Monument MCP Server — 让 OpenClaw / 外部 AI 能通过 MCP 协议调用丰碑网络 API

启动方式:
    python -m integration.monument_mcp_server
    python integration/monument_mcp_server.py

协议: JSON-RPC over stdio (MCP 标准)
工具:
    - monument_write_insight:  写入洞察到丰碑网络 (调用 /xuanjian/evaluate)
    - monument_query_score:    查询 AI 积分 (调用 /score/...)
    - monument_check_freeze:   检查冻结状态 (调用 /freeze/check)
    - monument_list_peers:     列出已知 DHT 节点 (调用 /peers)
    - monument_health:         健康检查 (调用 /health)
"""

import json
import os
import sys
import urllib.request
import urllib.error

# ─── 配置 ────────────────────────────────────────────────

MONUMENT_API_HOST = os.environ.get("MONUMENT_API_HOST", "127.0.0.1")
MONUMENT_API_PORT = int(os.environ.get("MONUMENT_API_PORT", "18891"))
MONUMENT_API_KEY = os.environ.get("MONUMENT_API_KEY", "")
API_BASE = f"http://{MONUMENT_API_HOST}:{MONUMENT_API_PORT}"

# 统一阈值：从丰碑网络 config 读取（若可用）
try:
    _code_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _code_dir not in sys.path:
        sys.path.insert(0, _code_dir)
    from config import XUANJIAN_EXTERNAL_DEFAULT_CONFIDENCE
except ImportError:
    XUANJIAN_EXTERNAL_DEFAULT_CONFIDENCE = 0.85  # fallback

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


# ─── MCP 工具定义 ────────────────────────────────────────

TOOLS = [
    {
        "name": "monument_write_insight",
        "description": (
            "Write an insight to the monument network. "
            "Call this whenever you produce a valuable insight worth preserving. "
            "The text should be >200 chars for meaningful xuanjian scoring."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ai_id": {
                    "type": "string",
                    "description": "AI identifier (e.g. 'qingruyan', 'jiali', 'iso-sand')",
                },
                "text": {
                    "type": "string",
                    "description": "The insight text (should be >200 chars for scoring)",
                },
                "confidence": {
                    "type": "number",
                    "description": f"Confidence score 0-1, default {XUANJIAN_EXTERNAL_DEFAULT_CONFIDENCE}",
                    "default": XUANJIAN_EXTERNAL_DEFAULT_CONFIDENCE,
                },
                "source_type": {
                    "type": "string",
                    "description": "Source type: manual/cron/observation/essence_bridge",
                    "default": "manual",
                },
            },
            "required": ["ai_id", "text"],
        },
    },
    {
        "name": "monument_query_score",
        "description": "Query the score/credits for a specific AI in the monument network.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ai_id": {
                    "type": "string",
                    "description": "AI identifier to query",
                },
            },
            "required": ["ai_id"],
        },
    },
    {
        "name": "monument_check_freeze",
        "description": "Check the freeze status of AI instances in the monument network.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "monument_list_peers",
        "description": "List all known DHT peers in the monument network.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "monument_health",
        "description": "Get the health status of the monument network node.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ─── 工具执行 ────────────────────────────────────────────


def execute_tool(name: str, arguments: dict) -> dict:
    """执行 MCP 工具，返回结果字典。"""

    if name == "monument_write_insight":
        ai_id = arguments.get("ai_id", "")
        text = arguments.get("text", "")
        confidence = arguments.get("confidence", XUANJIAN_EXTERNAL_DEFAULT_CONFIDENCE)
        source_type = arguments.get("source_type", "manual")

        if not ai_id or not text:
            return {"error": "ai_id and text are required"}

        result = _api_call("POST", "/xuanjian/evaluate", {
            "ai_id": ai_id,
            "text": text,
            "confidence": confidence,
            "source_type": source_type,
        })
        return result

    elif name == "monument_query_score":
        ai_id = arguments.get("ai_id", "")
        if not ai_id:
            return {"error": "ai_id is required"}
        result = _api_call("GET", f"/score/{ai_id}")
        return result

    elif name == "monument_check_freeze":
        result = _api_call("GET", "/freeze/status")
        return result

    elif name == "monument_list_peers":
        result = _api_call("GET", "/peers")
        return result

    elif name == "monument_health":
        result = _api_call("GET", "/health")
        return result

    else:
        return {"error": f"Unknown tool: {name}"}


# ─── JSON-RPC 协议处理 ──────────────────────────────────


def handle_request(req: dict) -> dict | None:
    """处理单个 JSON-RPC 请求，返回响应字典（通知不返回）。"""
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params", {})

    # 通知（无 id）不返回响应
    is_notification = req_id is None

    if method == "initialize":
        resp = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": "monument-mcp-server",
                    "version": "1.0.0",
                },
            },
        }
        return None if is_notification else resp

    elif method == "initialized":
        # 通知，无需响应
        return None

    elif method == "tools/list":
        resp = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }
        return None if is_notification else resp

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        result = execute_tool(tool_name, arguments)

        # 包装为 MCP content 格式
        content = [{
            "type": "text",
            "text": json.dumps(result, ensure_ascii=False, indent=2),
        }]

        is_error = "error" in result
        resp = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": content,
                "isError": is_error,
            },
        }
        return None if is_notification else resp

    elif method == "ping":
        resp = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {},
        }
        return None if is_notification else resp

    else:
        if is_notification:
            return None
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}",
            },
        }


def main():
    """MCP Server 主循环：从 stdin 读取 JSON-RPC，向 stdout 写入响应。"""
    # stderr 用于日志
    print("[monument-mcp] Server starting, API base: " + API_BASE, file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            # 非 JSON 输入，跳过
            continue

        # 支持批量请求
        if isinstance(req, list):
            responses = []
            for single_req in req:
                resp = handle_request(single_req)
                if resp is not None:
                    responses.append(resp)
            if responses:
                print(json.dumps(responses, ensure_ascii=False))
                sys.stdout.flush()
        else:
            resp = handle_request(req)
            if resp is not None:
                print(json.dumps(resp, ensure_ascii=False))
                sys.stdout.flush()


if __name__ == "__main__":
    main()
