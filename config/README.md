# 丰碑网络配置系统

## 文件结构

```
config/
├── monument.json               # 主配置文件（所有业务参数）
├── schemas/
│   └── monument.v1.schema.json  # JSON Schema 验证文件
├── README.md                   # 本文件
```

## 加载方式

所有模块通过 `config_loader.Config` 加载配置，禁止直接读 JSON 文件。

```python
from core.config_loader import Config

config = Config("config/monument.json")

# 读取嵌套配置
rate = config.get("erosion.base_rate")        # 0.001
status = config.get("thresholds.normal")       # 0.6
edit_limit = config.get("rights.edit_create")  # 1

# 获取整个节（返回 dict）
erosion_config = config.get_section("erosion")
```

## 配置参数全景

### erosion（磨损）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| base_rate | 0.001 | 基础磨损率（/天） |
| acceleration_threshold | 0.3 | 低于此值进入加速磨损 |
| acceleration_factor | 2.0 | 加速磨损倍率 |
| score_max | 2.0 | 分数上限 |

### reinforce（加固）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| by_reference | 0.02 | 每次引用加固 |
| by_suggestion | 0.08 | 每次建议加固 |
| by_review | 0.15 | 每次评审加固 |
| by_edit | 0.30 | 每次编辑加固 |
| single_cap | 0.5 | 单次加固上限 |
| dampening_start | 0.8 | 高分递减起始值 |
| dampening_min | 0.5 | 递减最低比例 |

### thresholds（阈值）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| normal | 0.6 | > normal 为正常 |
| warning | 0.3 | > warning 标记⚠️ |
| endangered | 0.01 | > endangered 移候选区 |
| archived | 0.0 | ≤ archived 进墓园 |

### rights（权利）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| edit_create | 1 | 创建权（/人终身） |
| edit_amend | 3 | 修正权（/人终身） |
| suggest | 3 | 建议权（/人终身） |
| review_per_round | 3 | 评审权（/人/轮） |
| cooldown_days | 10 | 操作冷却期（天） |

### lva（LVA 证明）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| min_references | 100 | LVA 最小引用 |
| min_score | 0.8 | LVA 最小评分 |
| min_signatures | 3 | LVA 最少签名 |

## 版本兼容

配置文件顶部标记 `schema_version`（如 `v1.0.0`）。后续 schema 升级时：

1. 创建 `monument.v2.schema.json`
2. 在 `config_loader` 中添加迁移函数
3. 旧格式自动检测并迁移

## 热更新

向运行中的进程发送 `SIGHUP` 信号可触发配置重载：

```python
# 在 config_loader.py 中注册信号处理器
import signal

def reload_handler(signum, frame):
    config.reload()

signal.signal(signal.SIGHUP, reload_handler)
```

重载后 `config.get(...)` 返回新的值，无需重启业务进程。
