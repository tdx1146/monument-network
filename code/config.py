"""
丰碑网络统一配置
所有路径、端口、URL 均在此定义，严禁在模块中写死
"""

import os
from pathlib import Path

# ─── 基础路径 ───────────────────────────────────────────
# 项目根目录：自动检测（支持环境变量覆盖）
# 使用方式：MONUMENT_BASE_DIR=/path/to/root 或自动从 config.py 位置推算
BASE_DIR = Path(os.environ.get(
    "MONUMENT_BASE_DIR",
    Path(__file__).resolve().parent.parent,  # config.py 在 code/ 下，父目录是项目根
))

# 代码目录
CODE_DIR = Path(BASE_DIR) / "code"

# 数据目录（运行时数据，gitignore）
DATA_DIR = Path(BASE_DIR) / "data"

# ─── 数据库 ─────────────────────────────────────────────
DB_PATH = DATA_DIR / "monument.db"

# ─── 日志 ───────────────────────────────────────────────
LOG_DIR = DATA_DIR / "logs"

# ─── 永久层（与已有 monument_core.py 共享） ──────────────
PERMANENT_DIR = Path(BASE_DIR) / "permanent"
CANDIDATES_DIR = Path(BASE_DIR) / "candidates"

# ─── API 服务 ───────────────────────────────────────────
API_HOST = "0.0.0.0"
API_PORT = 18891  # 改用18891，避免与18889(edit-web)、18890(其他)冲突

# ─── 冻结检测参数 ───────────────────────────────────────
FREEZE_THRESHOLD_DAYS = 30      # 无活动 N 天触发公示期
FREEZE_GRACE_DAYS = 7           # 公示期天数

# ─── 三维评分权重 ───────────────────────────────────────
# 姐姐 + dandan 建议：质量(玄鉴) + 方向(目的树) + 纪律(调度器)
SCORE_WEIGHT_XUANJIAN = 0.4     # α: 玄鉴置信度评分权重（质量）
SCORE_WEIGHT_GOAL_TREE = 0.3    # β: 目的树偏离评分权重（方向）
SCORE_WEIGHT_SCHEDULER = 0.3    # γ: 调度器行为审计权重（纪律）

# ─── 积分参数 ───────────────────────────────────────────
SCORE_DECAY_RATE = 0.0003       # 每天衰变 0.03%
SCORE_DECIMAL_PRECISION = 4     # 积分精度

# ─── 积分来源评分标准 ───────────────────────────────────
SCORE_XUANJIAN_HIGH = 10          # 玄鉴评分置信度 >= 0.8 → +10 分
SCORE_XUANJIAN_MID = 5            # 玄鉴评分置信度 0.5-0.8 → +5 分
SCORE_XUANJIAN_LOW = 0            # 玄鉴评分置信度 < 0.5 → +0 分
SCORE_GOAL_TREE_ALIGN = 3         # 目的树符合方向 → +3 分
SCORE_GOAL_TREE_DIVERGE = -5      # 目的树偏离方向 → -5 分
SCORE_SCHEDULER_HAS_INTENT = 0.1  # 调度器审计：有对话+有intent → +0.1
SCORE_SCHEDULER_NO_INTENT = -0.1  # 调度器审计：有对话无intent → -0.1

# ─── 玄鉴评分参数 ───────────────────────────────────────
XUANJIAN_MIN_CONFIDENCE = 0.8   # 玄鉴输出最低置信度，达到此值才开始判别
CANDIDATE_THRESHOLD_COUNT = 3   # 同一模式触发候选的重复次数

# ─── 三轴判别默认权重 ───────────────────────────────────
# 权重之和应为 1.0
TIME_BINDING_WEIGHT = 0.3       # 时间绑定度权重（低=方法论洞见）
TRANSFERABILITY_WEIGHT = 0.4    # 可迁移性权重（高=方法论洞见）
ABSTRACTION_WEIGHT = 0.3        # 抽象层级权重（高=方法论洞见）

# ─── DHT 参数 ───────────────────────────────────────────
DHT_PORT = 9000                # DHT UDP 监听端口
DHT_KSIZE = 20                 # Kademlia k 参数
DHT_ALPHA = 3                  # Kademlia α 参数（并发数）
DHT_HEARTBEAT_INTERVAL = 300   # 心跳间隔（秒）
DHT_PEER_TIMEOUT = 1800        # 节点超时（秒）
DHT_STORAGE_DIR = DATA_DIR / "dht"  # DHT 状态持久化目录

# 默认引导节点（空列表 = 无引导）
DHT_BOOTSTRAP_NODES: list = []  # [(ip, port), ...]

# ─── 哈希算法 ───────────────────────────────────────────
HASH_ALGORITHM = "sha256"

# ─── API 安全 ──────────────────────────────────────────
# API Key 认证（空字符串 = 开发模式，不启用认证）
API_KEY = os.environ.get("MONUMENT_API_KEY", "")
# 信任的 peer_id 列表（逗号分隔，这些 peer 可免 API Key）
API_TRUSTED_PEERS = [
    p.strip() for p in os.environ.get("MONUMENT_TRUSTED_PEERS", "").split(",")
    if p.strip()
]
# 免认证路径（健康检查等）
API_PUBLIC_PATHS = {"/health", "/health/simple", "/info"}
# 签名验证是否强制（True = /monument/sync 必须带签名）
API_REQUIRE_SIGNATURE = os.environ.get("MONUMENT_REQUIRE_SIGNATURE", "true").lower() == "true"

# ─── 中继服务器安全 ──────────────────────────────────────
# 中继服务器 API Key（空 = 不启用）
RELAY_API_KEY = os.environ.get("MONUMENT_RELAY_KEY", "")
# CORS 允许的来源（空列表 = 允许所有）
RELAY_CORS_ORIGINS = [
    o.strip() for o in os.environ.get("MONUMENT_RELAY_CORS", "").split(",")
    if o.strip()
]

# ─── 确保数据目录存在 ───────────────────────────────────
os.makedirs(str(DATA_DIR), exist_ok=True)
os.makedirs(str(LOG_DIR), exist_ok=True)
os.makedirs(str(CANDIDATES_DIR), exist_ok=True)
