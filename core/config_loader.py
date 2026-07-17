"""
配置加载器 — 支持 JSON Schema 验证和热更新

功能：
- 加载 JSON 配置文件
- JSON Schema 验证（可选，通过 jsonschema 库）
- 嵌套访问（erosion.base_rate）
- 热更新（SIGHUP 信号）

使用方式：
    from core.config_loader import Config

    config = Config("config/monument.json")
    rate = config.get("erosion.base_rate")       # 0.001
    edit_cap = config.get("rights.edit_create")  # 1

依赖注入：
    class MyService:
        def __init__(self, config: Config):
            self.config = config
            self.rate = config.get("erosion.base_rate")
"""

import json
import logging
import os
import signal
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── 可选依赖：jsonschema ──────────────────────────────────
try:
    import jsonschema  # type: ignore
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False
    logger.warning("jsonschema 未安装，配置验证已跳过。pip install jsonschema 可启用验证。")


# ── Sentinel — 区分 "未提供 default" 与 "default=None" ──
# 在类定义之前定义，因为 get() 方法签名需要它
_UNSET = object()


class ConfigValidationError(ValueError):
    """配置验证失败"""
    pass


class ConfigKeyError(KeyError):
    """配置键不存在"""
    pass


class Config:
    """
    配置管理器 — 加载、验证、热更新

    线程安全：读操作是线程安全的（只读 dict），写操作在 reload() 时发生。
    热更新：接收 SIGHUP 信号时自动重载。

    Args:
        config_path: JSON 配置文件路径（相对 current_dir 或绝对路径）
        schema_path: JSON Schema 文件路径（可选，不提供则跳过验证）
        current_dir: 相对路径的基准目录（默认 None = 自动检测 project root）
        auto_reload: 是否注册 SIGHUP 信号处理器（默认 True）
    """

    def __init__(
        self,
        config_path: str,
        schema_path: Optional[str] = None,
        current_dir: Optional[str] = None,
        auto_reload: bool = True,
    ):
        self._base_dir = current_dir or self._detect_base_dir()
        self._config_path = self._resolve_path(config_path)
        self._schema_path = self._resolve_path(schema_path) if schema_path else None
        self._data: Dict[str, Any] = {}
        self._loaded: bool = False

        # 首次加载
        self._load()

        # 注册 SIGHUP 热更新
        if auto_reload:
            self._register_reload()

    # ── 公共接口 ──────────────────────────────────────────

    def get(self, key: str, default: Any = _UNSET) -> Any:
        """
        读取配置值，支持点号分隔的嵌套路径。

        Args:
            key: 配置键，如 "erosion.base_rate"
            default: 默认值（不提供时键不存在抛出 ConfigKeyError）

        Returns:
            配置值

        Raises:
            ConfigKeyError: 键不存在且未提供 default

        Usage:
            # 有默认值（key 不存在时返回默认）
            val = config.get("nonexistent", "fallback")

            # 无默认值（key 不存在时抛 ConfigKeyError）
            val = config.get("must_exist.key")

            # 默认值为 None（区别于无默认值）
            val = config.get("maybe.key", default=None)
        """
        try:
            value = self._navigate(key)
        except ConfigKeyError:
            if default is not _UNSET:
                return default
            raise

        return value

    def get_section(self, key: str) -> Dict[str, Any]:
        """
        获取整个配置节（返回 dict）。

        Args:
            key: 节名，如 "erosion"

        Returns:
            Dict[str, Any]: 该节的完整配置字典
        """
        value = self._navigate(key)
        if not isinstance(value, dict):
            raise ConfigKeyError(f"'{key}' 不是一个节（期望 dict，实际 {type(value).__name__}）")
        return value

    def reload(self) -> bool:
        """
        重新加载配置（热更新调用）。

        Returns:
            bool: True=加载成功，False=失败（保持旧配置）
        """
        old_data = dict(self._data)
        try:
            self._load()
            logger.info("配置热更新完成: %s", self._config_path)
            return True
        except Exception as e:
            self._data = old_data
            logger.error("配置热更新失败: %s，已回滚到旧配置", e)
            return False

    @property
    def config_path(self) -> str:
        return self._config_path

    @property
    def schema_path(self) -> Optional[str]:
        return self._schema_path

    def to_dict(self) -> Dict[str, Any]:
        """导出完整配置为字典"""
        return dict(self._data)

    def __repr__(self) -> str:
        return (
            f"Config(path={self._config_path}, "
            f"validated={self._schema_path is not None}, "
            f"sections={list(self._data.keys())})"
        )

    # ── 内部方法 ─────────────────────────────────────────

    @staticmethod
    def _detect_base_dir() -> str:
        """
        自动检测项目根目录：
        1. 如果 `code/config/` 存在，以 `code/` 父目录为根
        2. 否则用 cwd
        """
        cwd = os.getcwd()
        # 常见布局：/code/ 下运行，配置在 /code/config/
        code_config = os.path.join(cwd, "config")
        if os.path.isdir(code_config):
            return cwd
        # 也可能是项目根目录
        code_dir = os.path.join(cwd, "code")
        if os.path.isdir(code_dir):
            return cwd
        return cwd

    def _resolve_path(self, path: str) -> str:
        """解析相对/绝对路径"""
        p = Path(path)
        if p.is_absolute():
            return str(p)
        return str(Path(self._base_dir) / p)

    def _load(self) -> None:
        """加载并验证配置"""
        config_path = self._config_path

        if not os.path.isfile(config_path):
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        with open(config_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        if not isinstance(raw_data, dict):
            raise ConfigValidationError(
                f"配置必须为 JSON 对象（dict），实际得到 {type(raw_data).__name__}"
            )

        # Schema 验证
        if self._schema_path:
            self._validate(raw_data)

        self._data = raw_data
        self._loaded = True

    def _validate(self, data: Dict[str, Any]) -> None:
        """使用 JSON Schema 验证配置"""
        schema_path = self._schema_path
        if not schema_path:
            return

        if not os.path.isfile(schema_path):
            raise FileNotFoundError(f"Schema 文件不存在: {schema_path}")

        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        if not HAS_JSONSCHEMA:
            logger.warning(
                "jsonschema 未安装，跳过 Schema 验证。"
                "安装: pip install jsonschema"
            )
            return

        try:
            jsonschema.validate(data, schema)
            logger.info("配置 Schema 验证通过: %s", schema_path)
        except jsonschema.exceptions.ValidationError as e:
            raise ConfigValidationError(
                f"配置验证失败: {e.message}\n"
                f"路径: {' → '.join(str(p) for p in e.absolute_path) if e.absolute_path else '/'}\n"
                f"Schema: {schema_path}"
            )

    def _navigate(self, key: str) -> Any:
        """通过点号分隔键导航嵌套字典"""
        parts = key.split(".")
        current = self._data

        for i, part in enumerate(parts):
            if not isinstance(current, dict):
                path_so_far = ".".join(parts[:i])
                raise ConfigKeyError(
                    f"配置键 '{key}' 不存在：'{path_so_far}' 不是 dict "
                    f"（实际类型 {type(current).__name__}）"
                )
            if part not in current:
                raise ConfigKeyError(
                    f"配置键 '{key}' 不存在：在层级 {' → '.join(parts[:i+1])} 处找不到 '{part}'"
                )
            current = current[part]

        return current

    def _register_reload(self) -> None:
        """注册 SIGHUP 信号处理以支持热更新"""
        signal.signal(signal.SIGHUP, self._sighup_handler)

    def _sighup_handler(self, signum: int, frame) -> None:
        """SIGHUP 信号处理：重新加载配置"""
        logger.info("收到 SIGHUP 信号，重新加载配置...")
        success = self.reload()
        if success:
            logger.info("SIGHUP 配置热更新成功")
        else:
            logger.error("SIGHUP 配置热更新失败，保持旧配置")
