#!/usr/bin/env python3
"""
丰碑网络一键部署引导脚本（Python 版本）

功能：
  - 检查运行环境（Python 版本、依赖）
  - 克隆/更新代码仓库
  - 安装 Python 依赖
  - 配置引导节点信息
  - 启动丰碑网络节点

用法：
  # 加入已有网络（指定引导节点）
  python3 bootstrap.py --bootstrap 192.168.0.149:9000

  # 独立启动（不指定引导节点，需手动组网）
  python3 bootstrap.py

  # 指定端口
  python3 bootstrap.py --bootstrap 192.168.0.149:9000 --api-port 18892 --dht-port 9001

  # 仅检查环境，不启动
  python3 bootstrap.py --check-only
"""

import argparse
import importlib
import importlib.util
import logging
import os
import platform
import shutil
import subprocess
import sys
import time


# ─── 配置 ─────────────────────────────────────────────────
DEFAULT_REPO_URL = "https://github.com/xxx/monument-network.git"
DEFAULT_INSTALL_DIR = "/opt/monument-network"
MIN_PYTHON_VERSION = (3, 11)


# ─── 日志 ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bootstrap")


# ─── 工具函数 ─────────────────────────────────────────────

def _run(cmd: list, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """运行命令并返回结果。"""
    logger.info("运行: %s", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def _python_version() -> tuple[int, int]:
    """返回 (major, minor) 版本元组。"""
    return (sys.version_info.major, sys.version_info.minor)


def _check_python() -> bool:
    """检查 Python 版本是否满足要求。"""
    version = _python_version()
    if version < MIN_PYTHON_VERSION:
        logger.error(
            "Python 版本过低: %s.%s（需要 %s.%s+）",
            version[0], version[1],
            MIN_PYTHON_VERSION[0], MIN_PYTHON_VERSION[1],
        )
        return False
    logger.info("Python %s.%s.%s ✅", version[0], version[1], sys.version_info.micro)
    return True


def _check_git() -> bool:
    """检查 git 是否可用。"""
    if not shutil.which("git"):
        logger.error("未找到 git，请先安装 git")
        return False
    logger.info("git %s", shutil.which("git"))
    return True


def _check_pip() -> bool:
    """检查 pip 是否可用。"""
    try:
        import pip
        logger.info("pip %s ✅", pip.__version__)
        return True
    except ImportError:
        logger.error("未找到 pip")
        return False


def _check_dependencies(install_dir: str) -> bool:
    """检查项目依赖是否已安装。"""
    req_file = os.path.join(install_dir, "code", "requirements.txt")
    if not os.path.exists(req_file):
        logger.warning("未找到 requirements.txt，跳过依赖检查")
        return True

    logger.info("检查依赖: %s", req_file)

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=columns"],
            capture_output=True, text=True, check=True,
        )
        installed = result.stdout.lower()

        with open(req_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                pkg_name = line.split("==")[0].split(">=")[0].split("<")[0].strip().lower()
                if pkg_name and pkg_name not in installed:
                    logger.warning("缺少依赖: %s", pkg_name)
                    return False
        return True
    except Exception as e:
        logger.warning("依赖检查失败（可忽略）: %s", e)
        return True


def _install_dependencies(install_dir: str) -> bool:
    """安装 Python 依赖。"""
    req_file = os.path.join(install_dir, "code", "requirements.txt")
    if not os.path.exists(req_file):
        logger.warning("未找到 requirements.txt，跳过依赖安装")
        return True

    logger.info("安装依赖...")
    try:
        # 优先使用 pip 的 --break-system-packages（Linux 新版本需要）
        cmd = [sys.executable, "-m", "pip", "install", "-r", req_file]
        is_debian_based = os.path.exists("/etc/debian_version")
        if is_debian_based:
            cmd.append("--break-system-packages")

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("依赖安装失败: %s", result.stderr.strip())
            return False
        logger.info("依赖安装完成 ✅")
        return True
    except Exception as e:
        logger.error("依赖安装出错: %s", e)
        return False


def _clone_or_update(install_dir: str, repo_url: str) -> bool:
    """克隆或更新代码仓库。"""
    if os.path.exists(install_dir):
        logger.info("仓库已存在，执行 git pull ...")
        try:
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=install_dir,
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                logger.info("代码已更新 ✅")
            else:
                logger.warning("更新失败: %s", result.stderr.strip())
        except Exception as e:
            logger.warning("git pull 出错: %s", e)
    else:
        logger.info("克隆仓库: %s", repo_url)
        try:
            parent_dir = os.path.dirname(install_dir)
            os.makedirs(parent_dir, exist_ok=True)
            result = subprocess.run(
                ["git", "clone", repo_url, install_dir],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                logger.error("克隆失败: %s", result.stderr.strip())
                return False
            logger.info("代码已克隆 ✅")
        except Exception as e:
            logger.error("克隆出错: %s", e)
            return False

    return True


def _start_node(args: argparse.Namespace) -> None:
    """启动丰碑网络节点。"""
    code_dir = os.path.join(args.install_dir, "code")

    # 配置环境变量
    env = os.environ.copy()
    env["MONUMENT_API_PORT"] = str(args.api_port)
    env["MONUMENT_DHT_PORT"] = str(args.dht_port)
    if args.bootstrap:
        env["MONUMENT_BOOTSTRAP_NODE"] = args.bootstrap

    # 构建启动参数
    cmd = [sys.executable, "-m", "api.app"]
    cmd.extend(["--api-port", str(args.api_port)])
    cmd.extend(["--dht-port", str(args.dht_port)])
    if args.bootstrap:
        cmd.extend(["--bootstrap", args.bootstrap])

    logger.info("=" * 50)
    logger.info("启动丰碑网络节点")
    logger.info("  安装目录: %s", args.install_dir)
    logger.info("  API 端口: %s", args.api_port)
    logger.info("  DHT 端口: %s", args.dht_port)
    if args.bootstrap:
        logger.info("  引导节点: %s", args.bootstrap)
    else:
        logger.info("  模式: 独立启动（无引导节点）")
    logger.info("=" * 50)
    print()

    # 切换到 code 目录并启动
    os.chdir(code_dir)
    sys.path.insert(0, code_dir)

    try:
        from api.app import main as app_main
        app_main()
    except ImportError as e:
        logger.error("导入 api.app 失败: %s", e)
        logger.error("请确保 code 目录正确且依赖已安装")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("节点已停止")


def main():
    parser = argparse.ArgumentParser(
        description="丰碑网络一键部署引导脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 加入已有网络
  %(prog)s --bootstrap 192.168.0.149:9000

  # 独立启动
  %(prog)s

  # 自定义端口
  %(prog)s --bootstrap 192.168.0.149:9000 --api-port 18892 --dht-port 9001

  # 仅检查环境
  %(prog)s --check-only
        """,
    )
    parser.add_argument(
        "--bootstrap",
        help="引导节点地址（格式：ip:port）",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=int(os.environ.get("MONUMENT_API_PORT", "18891")),
        help="HTTP API 端口（默认 18891）",
    )
    parser.add_argument(
        "--dht-port",
        type=int,
        default=int(os.environ.get("MONUMENT_DHT_PORT", "9000")),
        help="DHT UDP 端口（默认 9000）",
    )
    parser.add_argument(
        "--install-dir",
        default=os.environ.get("INSTALL_DIR", DEFAULT_INSTALL_DIR),
        help=f"安装目录（默认 {DEFAULT_INSTALL_DIR}）",
    )
    parser.add_argument(
        "--repo-url",
        default=os.environ.get("REPO_URL", DEFAULT_REPO_URL),
        help=f"代码仓库 URL（默认 {DEFAULT_REPO_URL}）",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="仅检查环境，不启动节点",
    )

    args = parser.parse_args()

    print()
    print("╔══════════════════════════════════════════╗")
    print("║    丰碑网络 - 自动部署脚本 (Python)     ║")
    print("╚══════════════════════════════════════════╝")
    print()

    # ── 环境检查 ──
    print("─── 环境检查 ───")
    checks = [
        ("Python 版本", _check_python()),
        ("git 可用", _check_git()),
        ("pip 可用", _check_pip()),
    ]
    for name, ok in checks:
        status = "✅" if ok else "❌"
        print(f"  {status} {name}")

    if not all(ok for _, ok in checks):
        logger.error("环境检查未通过，请修复后重试")
        sys.exit(1)

    if args.check_only:
        logger.info("环境检查完成，一切正常")
        sys.exit(0)

    # ── 获取/更新代码 ──
    print()
    print("─── 获取代码 ───")
    if not _clone_or_update(args.install_dir, args.repo_url):
        sys.exit(1)

    # ── 安装依赖 ──
    print()
    print("─── 安装依赖 ───")
    if not _install_dependencies(args.install_dir):
        logger.warning("依赖安装有警告，继续启动...")

    # ── 启动节点 ──
    print()
    _start_node(args)


if __name__ == "__main__":
    main()
