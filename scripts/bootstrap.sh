#!/bin/bash
# 丰碑网络一键部署脚本
#
# 用法：
#   ./bootstrap.sh <bootstrap_node_address>    # 从引导节点同步
#   ./bootstrap.sh                              # 独立启动（需手动组网）
#
# 示例：
#   ./bootstrap.sh 192.168.0.149:9000
#
# 环境变量（可在调用前设置）：
#   BOOTSTRAP_NODE   引导节点地址（同第一个参数）
#   REPO_URL         代码仓库 URL（默认示例 URL，需替换）
#   INSTALL_DIR      安装目录（默认 /opt/monument-network）
#   API_PORT         HTTP API 端口（默认 18891）
#   DHT_PORT         DHT UDP 端口（默认 9000）

set -e

# ─── 参数解析 ──────────────────────────────────────────
BOOTSTRAP_NODE=${1:-${BOOTSTRAP_NODE:-""}}
REPO_URL="${REPO_URL:-https://github.com/xxx/monument-network.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/monument-network}"
API_PORT="${API_PORT:-18891}"
DHT_PORT="${DHT_PORT:-9000}"

# ─── 颜色输出 ─────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[INFO]${NC}  $1"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo -e "\n${CYAN}══════════════════════════════════════════${NC}"
echo -e "${CYAN}    丰碑网络 - 自动部署脚本${NC}"
echo -e "${CYAN}══════════════════════════════════════════${NC}"
echo ""

if [ -n "$BOOTSTRAP_NODE" ]; then
    info "引导节点: $BOOTSTRAP_NODE"
else
    warn "未指定引导节点，将在独立模式下启动"
    warn "如需加入已有网络，请使用: $0 <ip:port>"
fi

# ───────────────────────────────────────────────────────
# Step 1: 检查 Python 版本
# ───────────────────────────────────────────────────────
echo -e "\n${YELLOW}[1/5]${NC} 检查 Python 环境..."

PYTHON=$(command -v python3 || command -v python || true)
if [ -z "$PYTHON" ]; then
    error "未找到 Python 3。请先安装 python3.11+"
    exit 1
fi

PYTHON_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]); then
    error "需要 Python 3.11+，当前为 $PYTHON_VERSION"
    exit 1
fi
ok "Python $PYTHON_VERSION ($PYTHON)"

# ───────────────────────────────────────────────────────
# Step 2: 克隆/更新代码
# ───────────────────────────────────────────────────────
echo -e "\n${YELLOW}[2/5]${NC} 获取源码..."

if [ ! -d "$INSTALL_DIR" ]; then
    info "克隆仓库到 $INSTALL_DIR ..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    ok "代码已克隆"
else
    info "仓库已存在，更新中..."
    cd "$INSTALL_DIR"
    git pull --ff-only 2>/dev/null && ok "代码已更新" || warn "更新失败，使用现有代码"
fi

cd "$INSTALL_DIR/code"

# ───────────────────────────────────────────────────────
# Step 3: 安装依赖
# ───────────────────────────────────────────────────────
echo -e "\n${YELLOW}[3/5]${NC} 安装 Python 依赖..."

if [ -f "requirements.txt" ]; then
    $PYTHON -m pip install -r requirements.txt --break-system-packages 2>&1 | tail -1
    ok "依赖安装完成"
else
    warn "未找到 requirements.txt，跳过依赖安装"
fi

# ───────────────────────────────────────────────────────
# Step 4: 配置环境
# ───────────────────────────────────────────────────────
echo -e "\n${YELLOW}[4/5]${NC} 配置运行时环境..."

# 导出引导节点信息（供 Python 代码读取）
if [ -n "$BOOTSTRAP_NODE" ]; then
    export MONUMENT_BOOTSTRAP_NODE="$BOOTSTRAP_NODE"
    # 解析 IP 和端口，写入 config 兼容格式
    BOOTSTRAP_IP=$(echo "$BOOTSTRAP_NODE" | cut -d: -f1)
    BOOTSTRAP_PORT=$(echo "$BOOTSTRAP_NODE" | cut -d: -f2)
    export MONUMENT_BOOTSTRAP_IP="$BOOTSTRAP_IP"
    export MONUMENT_BOOTSTRAP_PORT="$BOOTSTRAP_PORT"
    ok "引导节点: $BOOTSTRAP_NODE"
fi

export MONUMENT_API_PORT="$API_PORT"
export MONUMENT_DHT_PORT="$DHT_PORT"
ok "API 端口: $API_PORT"
ok "DHT 端口: $DHT_PORT"

# 确保数据目录存在
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/data/logs" "$INSTALL_DIR/data/dht" 2>/dev/null

# ───────────────────────────────────────────────────────
# Step 5: 启动节点
# ───────────────────────────────────────────────────────
echo -e "\n${YELLOW}[5/5]${NC} 启动丰碑网络节点..."

# 构建启动参数
ARGS="--api-port $API_PORT --dht-port $DHT_PORT"
if [ -n "$BOOTSTRAP_NODE" ]; then
    ARGS="$ARGS --bootstrap $BOOTSTRAP_NODE"
fi

echo ""
info "启动命令: $PYTHON -m api.app $ARGS"
echo ""

cd "$INSTALL_DIR/code"
exec $PYTHON -m api.app $ARGS
