"""
MCP 服务互通端点 —— 工具列表 + 工具调用

端点：
    POST /mcp/tools/list    返回可用工具列表（JSON-RPC 风格）
    POST /mcp/tools/call    调用指定工具

工具：
    - xuanjian_evaluate: 对 AI 洞察文本进行三轴评分
"""

import json
import sys
import os

from flask import Blueprint, request, jsonify

# 确保 code/ 目录在 sys.path 中
_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from core.xuanjian_pipe import XuanjianPipe, InsightSource
from config import XUANJIAN_EXTERNAL_DEFAULT_CONFIDENCE

bp = Blueprint("mcp", __name__)


# ─── 工具列表 ────────────────────────────────────────────


@bp.route("/tools/list", methods=["POST"])
def tools_list():
    """返回 MCP 可用的工具列表及其 Schema。"""
    return jsonify({
        "tools": [{
            "name": "xuanjian_evaluate",
            "description": "对 AI 洞察文本进行三轴评分（质量/方向/纪律）",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "洞察文本"},
                    "ai_id": {"type": "string", "description": "AI 标识"},
                    "confidence": {
                        "type": "number",
                        "description": "外部输入置信度（可选，默认从配置读取）",
                    },
                },
                "required": ["text", "ai_id"],
            },
        }]
    })


# ─── 工具调用 ────────────────────────────────────────────


@bp.route("/tools/call", methods=["POST"])
def tools_call():
    """调用 MCP 工具。"""
    data = request.get_json(force=True)

    if not data or "name" not in data:
        return jsonify({"error": "Missing 'name' in request body"}), 400

    tool_name = data.get("name")
    arguments = data.get("arguments", {})

    if tool_name != "xuanjian_evaluate":
        return jsonify({"error": f"Unknown tool: {tool_name}"}), 400

    text = arguments.get("text")
    ai_id = arguments.get("ai_id")
    confidence = arguments.get("confidence", XUANJIAN_EXTERNAL_DEFAULT_CONFIDENCE)

    if not text:
        return jsonify({"error": "Missing 'text' in arguments"}), 400
    if not ai_id:
        return jsonify({"error": "Missing 'ai_id' in arguments"}), 400

    # 调用玄鉴评分（必须传入 source 参数，与 xuanjian_routes 签名一致）
    try:
        pipe = XuanjianPipe()
        source = InsightSource(source_type="mcp")
        analysis = pipe.evaluate(
            ai_id=ai_id,
            text=text,
            confidence=confidence,
            source=source,
        )
    except Exception as e:
        return jsonify({
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "error": str(e),
                }, ensure_ascii=False),
            }]
        }), 500

    return jsonify({
        "content": [{
            "type": "text",
            "text": json.dumps({
                "monument_score": analysis.monument_score,
                "time_binding": analysis.time_binding,
                "transferability": analysis.transferability,
                "abstraction_level": analysis.abstraction_level,
                "confidence": analysis.confidence,
                "is_candidate": analysis.is_candidate,
                "pattern_key": analysis.pattern_key,
                "pattern_count": analysis.pattern_count,
            }, ensure_ascii=False),
        }]
    })
