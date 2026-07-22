"""
丰碑网络 HTTP API 服务 —— Flask 应用

启动方式：
    python3 -m api.app                         # 独立启动
    python3 -m api.app --bootstrap ip:port      # 使用引导节点启动
    python3 -m api.app --api-port 18892         # 指定 API 端口
    python3 -m api.app --dht-port 9001          # 指定 DHT 端口

端点一览：
    POST /monument/sync         # 接收丰碑（跨实例同步，支持信封格式）
    GET  /monument/query        # 查询丰碑（支持 since 时间戳过滤，返回信封格式）
    GET  /health                # 健康检查
    GET  /info                  # 节点信息（含信封元信息）
    GET  /peers                 # DHT 已知节点列表

DHT 节点发现：
    启动时自动启动 Kademlia DHT 节点（UDP），用于自动发现网络中
    的其他丰碑节点。节点地址通过 DHT 注册和查询。
"""

import sys
import os
import asyncio
import argparse
import threading

# 确保 code/ 目录在 sys.path 中
_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

import logging
from flask import Flask, jsonify
from config import API_HOST, API_PORT, LOG_DIR, DHT_PORT, DHT_BOOTSTRAP_NODES, DATA_DIR

# DHT 状态持久化目录
DHT_STORAGE_DIR = DATA_DIR / "dht"

# 模块级日志器
logger = logging.getLogger("monument-api")

# ─── 全局 DHT 节点实例 ───────────────────────────────────
_dht_node_instance = None
_dht_peer_id = None
_dht_loop = None

# 延迟导入，避免循环依赖

def _register_routes(app):
    from api.monument_routes import register_monument_routes
    register_monument_routes(app)

    # MCP 工具端点
    from api.mcp_routes import bp as mcp_bp
    app.register_blueprint(mcp_bp, url_prefix='/mcp')


# ─── DHT 节点发现 ──────────────────────────────────────────

def get_dht_node():
    """获取全局 DHT 节点实例。"""
    global _dht_node_instance
    return _dht_node_instance


def get_dht_peer_id():
    """获取 DHT 节点在 DHT 网络中的 peer ID（十六进制）。"""
    global _dht_peer_id
    return _dht_peer_id


def _dht_register_peer(dht_node, http_address: str, peer_id: str):
    """在 DHT 网络中注册本节点。"""
    async def _register():
        await dht_node.register(peer_id, [http_address])
    try:
        future = asyncio.run_coroutine_threadsafe(
            _register(), _dht_loop
        )
        future.result(timeout=5)
        logger.info("DHT 节点已注册: peer_id=%s, address=%s", peer_id[:16], http_address)
    except Exception as e:
        logger.warning("DHT 节点注册失败: %s", e)


def _dht_discover_peers():
    """执行一轮 DHT 节点发现，返回 {peer_id: address} 字典。"""
    global _dht_node_instance, _dht_loop
    if not _dht_node_instance or not _dht_loop:
        return {}
    async def _discover():
        await _dht_node_instance.discover_peers()
        return await _dht_node_instance.list_peers()
    try:
        future = asyncio.run_coroutine_threadsafe(
            _discover(), _dht_loop
        )
        return future.result(timeout=10)
    except Exception as e:
        logger.warning("DHT 节点发现失败: %s", e)
        return {}


def _start_dht_background(http_address: str, peer_id: str):
    """在后台线程中启动 DHT 节点。"""
    global _dht_node_instance, _dht_peer_id, _dht_loop

    async def _run_dht():
        global _dht_loop
        _dht_loop = asyncio.get_running_loop()

        from core.dht_node import DHTNode
        node = DHTNode(storage_path=str(DHT_STORAGE_DIR))
        global _dht_node_instance
        _dht_node_instance = node

        await node.start(port=DHT_PORT)
        logger.info("DHT 节点已启动: DHT-ID=%s, port=%d", node.node_id[:16], DHT_PORT)

        # 注册本节点 HTTP 地址（安全调用，失败不影响主服务）
        try:
            await node.register(peer_id, [http_address])
        except Exception as e:
            logger.warning("DHT 注册失败（kademlia 版本可能不兼容）: %s", e)

        # 保持事件循环运行
        while True:
            await asyncio.sleep(3600)

    def _run():
        asyncio.run(_run_dht())

    thread = threading.Thread(target=_run, daemon=True, name="dht-node")
    thread.start()
    logger.info("DHT 后台线程已启动")





def create_app() -> Flask:
    """
    创建并配置 Flask 应用。

    返回:
        Flask 应用实例（尚未运行）
    """
    app = Flask(__name__)

    # ── 日志配置 ──────────────────────────────────────────
    os.makedirs(LOG_DIR, exist_ok=True)
    handler = logging.FileHandler(os.path.join(LOG_DIR, "api.log"), encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)

    # ── 路由注册 ──────────────────────────────────────────
    _register_routes(app)

    @app.route("/health")
    def health():
        """健康检查端点——调用 HealthChecker 返回完整报告。"""
        from core.health_checker import HealthChecker
        checker = HealthChecker()
        report = checker.run_all()
        return jsonify(report)

    @app.route("/health/simple")
    def health_simple():
        """简洁版健康检查——仅返回状态和基础信息。"""
        from core.health_checker import HealthChecker
        checker = HealthChecker()
        report = checker.run_all()
        return jsonify({
            "status": report["status"],
            "timestamp": report["timestamp"],
            "uptime_seconds": report["uptime_seconds"],
        })

    @app.route("/info")
    def info():
        """返回本节点基本信息。"""
        from core.p2p_network import P2PIdentity
        # 尝试加载持久化身份，或创建临时身份
        try:
            ident_path = _get_identity_path()
            if os.path.exists(ident_path):
                with open(ident_path, "rb") as f:
                    priv_key = f.read()
                identity = P2PIdentity(private_key=priv_key)
            else:
                identity = P2PIdentity()
        except Exception:
            identity = P2PIdentity()

        dht_id = get_dht_peer_id()
        return {
            "peer_id": identity.peer_id,
            "dht_node_id": dht_id,
            "protocol": "monument-exchange-v1",
            "node": API_HOST,
            "port": API_PORT,
        }

    @app.route("/peers")
    def peers():
        """返回 DHT 已知节点列表。"""
        dht_node = get_dht_node()
        if dht_node is None:
            return jsonify({
                "status": "error",
                "message": "DHT 节点未启动",
                "peers": []
            })

        peers_dict = _dht_discover_peers()
        peers_list = [
            {"peer_id": pid, "address": addr}
            for pid, addr in peers_dict.items()
        ]

        return jsonify({
            "status": "ok",
            "dht_port": DHT_PORT,
            "peer_count": len(peers_list),
            "peers": peers_list
        })

    return app


def _get_identity_path() -> str:
    """获取身份密钥文件路径。"""
    from config import DATA_DIR
    return os.path.join(DATA_DIR, "p2p_identity.key")


def _ensure_identity() -> str:
    """
    确保节点有持久化身份。如果不存在则创建新密钥对。

    返回:
        peer_id 字符串
    """
    from core.p2p_network import P2PIdentity

    ident_path = _get_identity_path()
    if os.path.exists(ident_path):
        with open(ident_path, "rb") as f:
            priv_key = f.read()
        identity = P2PIdentity(private_key=priv_key)
    else:
        identity = P2PIdentity()
        os.makedirs(os.path.dirname(ident_path), exist_ok=True)
        with open(ident_path, "wb") as f:
            f.write(identity.private_key_bytes)
        os.chmod(ident_path, 0o600)  # P0-2: 收紧密钥文件权限，防止其他进程读取私钥
        import logging
        logging.getLogger("monument-api").info("生成新 P2P 身份: peer_id=%s", identity.peer_id)

    return identity


# ─── 命令行参数 ──────────────────────────────────────────

def parse_args():
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="丰碑网络 HTTP API 服务",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 独立启动
  python3 -m api.app

  # 使用引导节点
  python3 -m api.app --bootstrap 192.168.0.149:9000

  # 变更端口
  python3 -m api.app --api-port 18892 --dht-port 9001
        """,
    )
    parser.add_argument(
        "--bootstrap",
        help="引导节点地址（格式：ip:port）",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=int(os.environ.get("MONUMENT_API_PORT", str(API_PORT))),
        help=f"HTTP API 端口（默认 {API_PORT}）",
    )
    parser.add_argument(
        "--dht-port",
        type=int,
        default=int(os.environ.get("MONUMENT_DHT_PORT", str(DHT_PORT))),
        help=f"DHT UDP 端口（默认 {DHT_PORT}）",
    )
    return parser.parse_args()


# ─── 独立启动入口 ──────────────────────────────────────────

def main():
    """独立启动入口。"""
    args = parse_args()
    app = create_app()
    
    # 如果指定了引导节点，更新 DHT 配置
    if args.bootstrap:
        ip, port_str = args.bootstrap.split(":")
        port = int(port_str)
        dht_bootstrap = [(ip, port)]
        logger.info("引导节点已指定: %s:%d (DHT bootstrap)", ip, port)
        # 更新模块级引导节点配置，使 DHT 连接时使用
        import config
        config.DHT_BOOTSTRAP_NODES = dht_bootstrap
    
    # 更新端口配置（如果指定了自定义端口）
    import config
    use_api_port = args.api_port
    use_dht_port = args.dht_port
    config.API_PORT = use_api_port
    config.DHT_PORT = use_dht_port
    
    # 获取节点身份（延迟初始化，触发身份文件创建）
    from api.monument_routes import _get_identity
    identity = _get_identity()
    
    # ── 启动 DHT 节点 ────────────────────────────────────
    http_address = f"{API_HOST}:{use_api_port}"
    _start_dht_background(http_address, identity.peer_id)
    
    app.logger.info(
        "丰碑网络节点启动: peer_id=%s  http://%s:%d  DHT-port=%d",
        identity.peer_id,
        API_HOST,
        use_api_port,
        use_dht_port,
    )
    print(f"\n  ✅ 丰碑网络节点已启动")
    print(f"  🆔  PeerID:  {identity.peer_id}")
    print(f"  🌐  地址:    http://{API_HOST}:{use_api_port}")
    print(f"  📡  DHT:     UDP {use_dht_port}")
    print(f"  📋  端点:")
    print(f"       POST /monument/sync    (接收丰碑，支持信封格式)")
    print(f"       GET  /monument/query   (查询丰碑, 返回信封格式)")
    print(f"       GET  /peers           (DHT 节点列表)")
    print(f"       GET  /health           (健康检查)")
    print(f"       GET  /info             (节点信息)")
    if args.bootstrap:
        print(f"  🔗  引导节点: {args.bootstrap}")
    print()
    app.run(host=API_HOST, port=use_api_port, debug=False)


if __name__ == "__main__":
    main()
