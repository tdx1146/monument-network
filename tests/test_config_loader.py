"""
测试：配置加载器 (config_loader.py)
"""

import json
import os
import signal
import tempfile
import time

import pytest

from core.config_loader import (
    Config,
    ConfigValidationError,
    ConfigKeyError,
    HAS_JSONSCHEMA,
)


# ── 夹具 ─────────────────────────────────────────────────

@pytest.fixture
def valid_config_path():
    """返回项目配置文件的路径"""
    return "config/monument.json"


@pytest.fixture
def valid_schema_path():
    """返回 schema 文件的路径"""
    return "config/schemas/monument.v1.schema.json"


@pytest.fixture
def minimal_config():
    """创建一个最简配置文件（用于测试）"""
    data = {
        "$schema": "./schemas/monument.v1.schema.json",
        "schema_version": "v1.0.0",
        "erosion": {
            "base_rate": 0.001,
            "acceleration_threshold": 0.3,
            "acceleration_factor": 2.0,
            "score_max": 2.0,
        },
        "reinforce": {
            "by_reference": 0.02,
            "by_suggestion": 0.08,
            "by_review": 0.15,
            "by_edit": 0.30,
            "single_cap": 0.5,
            "dampening_start": 0.8,
            "dampening_min": 0.5,
        },
        "thresholds": {
            "normal": 0.6,
            "warning": 0.3,
            "endangered": 0.01,
            "archived": 0.0,
        },
        "rights": {
            "edit_create": 1,
            "edit_amend": 3,
            "suggest": 3,
            "review_per_round": 3,
            "cooldown_days": 10,
        },
        "lva": {
            "min_references": 100,
            "min_score": 0.8,
            "min_signatures": 3,
        },
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(data, f)
        path = f.name
    yield path
    os.unlink(path)


# ── 测试：基本加载 ───────────────────────────────────────

class TestLoadConfig:

    def test_load_valid_config(self, valid_config_path):
        """能正确加载配置文件"""
        config = Config(valid_config_path, auto_reload=False)
        assert config is not None
        assert config.config_path.endswith("monument.json")
        assert config.schema_path is None  # 未指定 schema_path
        assert config.to_dict() != {}

    def test_load_with_schema(self, valid_config_path, valid_schema_path):
        """带 Schema 验证加载"""
        config = Config(valid_config_path, schema_path=valid_schema_path, auto_reload=False)
        assert config.schema_path is not None
        assert config.schema_path.endswith("monument.v1.schema.json")

    def test_load_file_not_found(self):
        """文件不存在时抛出 FileNotFoundError"""
        with pytest.raises(FileNotFoundError):
            Config("nonexistent.json", auto_reload=False)

    def test_load_invalid_json(self):
        """无效 JSON 内容抛出异常"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            f.write("this is not json{{")
            path = f.name
        with pytest.raises(json.JSONDecodeError):
            Config(path, auto_reload=False)
        os.unlink(path)

    def test_load_non_object_json(self):
        """JSON 顶层不是 object 时抛出 ConfigValidationError"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump([1, 2, 3], f)
            path = f.name
        with pytest.raises(ConfigValidationError, match="必须为 JSON 对象"):
            Config(path, auto_reload=False)
        os.unlink(path)


# ── 测试：嵌套访问 ───────────────────────────────────────

class TestNestedAccess:

    def test_get_simple_key(self, valid_config_path):
        config = Config(valid_config_path, auto_reload=False)
        assert config.get("schema_version") == "v1.0.0"

    def test_get_nested_key(self, valid_config_path):
        config = Config(valid_config_path, auto_reload=False)
        assert config.get("erosion.base_rate") == 0.001
        assert config.get("thresholds.normal") == 0.6
        assert config.get("rights.edit_create") == 1

    def test_get_nested_deep(self, valid_config_path):
        config = Config(valid_config_path, auto_reload=False)
        assert config.get("erosion.acceleration_threshold") == 0.3
        assert config.get("reinforce.by_reference") == 0.02
        assert config.get("lva.min_references") == 100

    def test_get_with_default(self, valid_config_path):
        config = Config(valid_config_path, auto_reload=False)
        assert config.get("nonexistent.key", "fallback") == "fallback"
        assert config.get("erosion.nonexistent", 42) == 42

    def test_get_with_default_none(self, valid_config_path):
        config = Config(valid_config_path, auto_reload=False)
        assert config.get("nonexistent", default=None) is None
        assert config.get("erosion.bogus", default=None) is None

    def test_get_without_default_raises(self, valid_config_path):
        config = Config(valid_config_path, auto_reload=False)
        with pytest.raises(ConfigKeyError):
            config.get("nonexistent.key")
        with pytest.raises(ConfigKeyError):
            config.get("erosion.nonexistent")

    def test_navigate_through_non_dict_raises(self, valid_config_path):
        config = Config(valid_config_path, auto_reload=False)
        # erosion.base_rate 是 0.001（float），再往下导航会出错
        with pytest.raises(ConfigKeyError, match="不是 dict"):
            config.get("erosion.base_rate.something")


# ── 测试：配置节 ─────────────────────────────────────────

class TestGetSection:

    def test_get_section_returns_dict(self, valid_config_path):
        config = Config(valid_config_path, auto_reload=False)
        section = config.get_section("erosion")
        assert isinstance(section, dict)
        assert section["base_rate"] == 0.001
        assert section["acceleration_threshold"] == 0.3
        assert section["acceleration_factor"] == 2.0
        assert section["score_max"] == 2.0

    def test_get_section_nested(self, valid_config_path):
        config = Config(valid_config_path, auto_reload=False)
        thresholds = config.get_section("thresholds")
        assert thresholds["normal"] == 0.6
        assert thresholds["warning"] == 0.3

    def test_get_section_non_dict_raises(self, valid_config_path):
        config = Config(valid_config_path, auto_reload=False)
        with pytest.raises(ConfigKeyError, match="不是一个节"):
            config.get_section("schema_version")

    def test_get_section_not_found_raises(self, valid_config_path):
        config = Config(valid_config_path, auto_reload=False)
        with pytest.raises(ConfigKeyError):
            config.get_section("nonexistent")


# ── 测试：Schema 验证 ────────────────────────────────────

class TestSchemaValidation:

    def test_valid_config_passes_schema(self, valid_config_path, valid_schema_path):
        """合法配置应通过 Schema 验证"""
        config = Config(valid_config_path, schema_path=valid_schema_path, auto_reload=False)
        assert config.get("erosion.base_rate") == 0.001

    def test_missing_required_field_fails(self, minimal_config, valid_schema_path):
        """缺少必填字段时抛出 ConfigValidationError"""
        # 修改配置，删除一个必填字段
        with open(minimal_config, "r") as f:
            data = json.load(f)
        del data["erosion"]["base_rate"]
        with open(minimal_config, "w") as f:
            json.dump(data, f)

        # 只有安装了 jsonschema 才测试 Schema 验证
        if HAS_JSONSCHEMA:
            with pytest.raises(ConfigValidationError):
                Config(minimal_config, schema_path=valid_schema_path, auto_reload=False)
        else:
            pytest.skip("jsonschema 未安装，跳过 Schema 验证测试")

    def test_invalid_type_fails(self, minimal_config, valid_schema_path):
        """类型错误时抛出 ConfigValidationError"""
        with open(minimal_config, "r") as f:
            data = json.load(f)
        # 把 base_rate 改成 string
        data["erosion"]["base_rate"] = "not_a_number"
        with open(minimal_config, "w") as f:
            json.dump(data, f)

        if HAS_JSONSCHEMA:
            with pytest.raises(ConfigValidationError):
                Config(minimal_config, schema_path=valid_schema_path, auto_reload=False)
        else:
            pytest.skip("jsonschema 未安装，跳过 Schema 验证测试")

    def test_additional_properties_fails(self, minimal_config, valid_schema_path):
        """额外的属性应被拒绝"""
        with open(minimal_config, "r") as f:
            data = json.load(f)
        data["extra_field"] = "should not be here"
        with open(minimal_config, "w") as f:
            json.dump(data, f)

        if HAS_JSONSCHEMA:
            with pytest.raises(ConfigValidationError):
                Config(minimal_config, schema_path=valid_schema_path, auto_reload=False)
        else:
            pytest.skip("jsonschema 未安装，跳过 Schema 验证测试")

    def test_schema_file_not_found(self, minimal_config):
        """Schema 文件不存在时抛出 FileNotFoundError"""
        with pytest.raises(FileNotFoundError):
            Config(minimal_config, schema_path="/nonexistent/schema.json", auto_reload=False)


# ── 测试：默认值 ─────────────────────────────────────────

class TestDefaultValues:

    def test_default_ui_version(self, valid_config_path):
        """测试获取所有默认值"""
        config = Config(valid_config_path, auto_reload=False)

        # erosion
        assert config.get("erosion.base_rate") == 0.001
        assert config.get("erosion.acceleration_threshold") == 0.3
        assert config.get("erosion.acceleration_factor") == 2.0
        assert config.get("erosion.score_max") == 2.0

        # reinforce
        assert config.get("reinforce.by_reference") == 0.02
        assert config.get("reinforce.by_suggestion") == 0.08
        assert config.get("reinforce.by_review") == 0.15
        assert config.get("reinforce.by_edit") == 0.30
        assert config.get("reinforce.single_cap") == 0.5
        assert config.get("reinforce.dampening_start") == 0.8
        assert config.get("reinforce.dampening_min") == 0.5

        # thresholds
        assert config.get("thresholds.normal") == 0.6
        assert config.get("thresholds.warning") == 0.3
        assert config.get("thresholds.endangered") == 0.01
        assert config.get("thresholds.archived") == 0.0

        # rights
        assert config.get("rights.edit_create") == 1
        assert config.get("rights.edit_amend") == 3
        assert config.get("rights.suggest") == 3
        assert config.get("rights.review_per_round") == 3
        assert config.get("rights.cooldown_days") == 10

        # lva
        assert config.get("lva.min_references") == 100
        assert config.get("lva.min_score") == 0.8
        assert config.get("lva.min_signatures") == 3


# ── 测试：热更新 ─────────────────────────────────────────

class TestReload:

    def test_reload_updated_values(self, minimal_config):
        """修改文件后，reload 应反映新值"""
        config = Config(minimal_config, auto_reload=False)
        assert config.get("erosion.base_rate") == 0.001

        # 修改文件
        with open(minimal_config, "r") as f:
            data = json.load(f)
        data["erosion"]["base_rate"] = 0.005
        with open(minimal_config, "w") as f:
            json.dump(data, f)

        # 热更新
        success = config.reload()
        assert success is True
        assert config.get("erosion.base_rate") == 0.005

    def test_reload_invalid_file_preserves_old(self, minimal_config):
        """加载损坏文件后应保持旧配置"""
        config = Config(minimal_config, auto_reload=False)
        assert config.get("erosion.base_rate") == 0.001

        # 写入损坏的 JSON
        with open(minimal_config, "w") as f:
            f.write("not valid json{")

        success = config.reload()
        assert success is False
        # 旧配置应保持
        assert config.get("erosion.base_rate") == 0.001

    def test_reload_twice(self, minimal_config):
        """连续 reload 两次应该正常工作"""
        config = Config(minimal_config, auto_reload=False)

        with open(minimal_config, "r") as f:
            data = json.load(f)
        data["erosion"]["base_rate"] = 0.005
        data["thresholds"]["normal"] = 0.7
        with open(minimal_config, "w") as f:
            json.dump(data, f)

        assert config.reload() is True
        assert config.get("erosion.base_rate") == 0.005
        assert config.get("thresholds.normal") == 0.7

        data["erosion"]["base_rate"] = 0.01
        with open(minimal_config, "w") as f:
            json.dump(data, f)

        assert config.reload() is True
        assert config.get("erosion.base_rate") == 0.01

    def test_to_dict_updated_after_reload(self, minimal_config):
        """reload 后 to_dict() 应返回新数据"""
        config = Config(minimal_config, auto_reload=False)

        with open(minimal_config, "r") as f:
            data = json.load(f)
        data["erosion"]["base_rate"] = 0.009
        with open(minimal_config, "w") as f:
            json.dump(data, f)

        config.reload()
        d = config.to_dict()
        assert d["erosion"]["base_rate"] == 0.009


# ── 测试：to_dict ────────────────────────────────────────

class TestToDict:

    def test_to_dict_returns_copy(self, valid_config_path):
        config = Config(valid_config_path, auto_reload=False)
        d = config.to_dict()
        d["fake_key"] = "value"
        # 原配置不应被修改
        with pytest.raises(ConfigKeyError):
            config.get("fake_key")

    def test_to_dict_content(self, valid_config_path):
        config = Config(valid_config_path, auto_reload=False)
        d = config.to_dict()
        assert d["erosion"]["base_rate"] == 0.001
        assert "thresholds" in d
        assert "rights" in d
        assert "lva" in d


# ── 测试：repr ────────────────────────────────────────────

class TestRepr:

    def test_repr(self, valid_config_path):
        config = Config(valid_config_path, auto_reload=False)
        r = repr(config)
        assert "Config(" in r
        assert "monument.json" in r
        assert "sections=" in r


# ── 测试：基准目录检测 ────────────────────────────────────

class TestBaseDirDetection:

    def test_current_dir_override(self, minimal_config):
        """通过 current_dir 参数指定基准目录"""
        cwd = os.getcwd()
        config = Config(
            minimal_config,
            current_dir=cwd,
            auto_reload=False,
        )
        assert config.get("erosion.base_rate") == 0.001
