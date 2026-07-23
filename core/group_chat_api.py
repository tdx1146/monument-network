#!/usr/bin/env python3
"""
AI群聊 HTTP 服务入口

将所有群聊模块串起来，提供统一的 HTTP API。

核心端点:
- POST /chat      — 接收用户消息，返回 AI 回复
- POST /message   — 消息进入群聊流（秘书长处理）
- GET /round/{id} — 查询轮次状态
- GET /scores     — 查询 AI 质量分
- POST /dispatch  — 手动 @ 指定 AI

调用链:
用户消息 → 秘书长判断 → 分发器调度 → 多AI并发 → 评分 → 摘要 → 返回

作者: Astron (子代理)
日期: 2026-06-29
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, validator

# 导入群聊模块
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from secretary_orchestrator import SecretaryOrchestrator, create_secretary
from dispatcher import Dispatcher, DispatchResult, AI_ROLES
from score_calculator import ScoreCalculator, calculate_score
from summary_generator import SummaryGenerator
from qdrant_client import QdrantClient, InMemoryStorage, create_client

# 导入救援AI
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_group_chat'))
from specialists.ai_member_rescuer import Rescuer as AIMemberRescuer
from specialists.rescuer import RescuerAI

# ============================================================
# 日志配置
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================
# 配置管理
# ============================================================

class Config:
    """服务配置 — 所有敏感值从环境变量读取"""

    # OpenClaw Proxy 配置（从环境变量读取）
    OPENCLAW_PROXY_URL: str = os.environ.get("OPENCLAW_PROXY_URL", "http://127.0.0.1:18887/v1")
    OPENCLAW_API_KEY: Optional[str] = os.environ.get("OPENCLAW_API_KEY")

    # Qdrant 配置
    QDRANT_HOST: str = os.environ.get("QDRANT_HOST", "localhost")
    QDRANT_PORT: int = int(os.environ.get("QDRANT_PORT", "6333"))
    QDRANT_HTTPS: bool = False
    QDRANT_API_KEY: Optional[str] = os.environ.get("QDRANT_API_KEY")
    QDRANT_USE_MEMORY: bool = True

    # 群聊参数
    DEFAULT_GROUP_ID: str = "default"
    ROUND_TIMEOUT_SECONDS: int = 300
    QUALITY_THRESHOLD: float = 0.5
    MAX_CONSECUTIVE_SPEAKS: int = 2

    # 服务配置
    API_PORT: int = int(os.environ.get("GROUP_CHAT_PORT", "18890"))
    DEBUG: bool = os.environ.get("GROUP_CHAT_DEBUG", "").lower() == "true"

    # 安全配置
    API_KEY: str = os.environ.get("GROUP_CHAT_API_KEY", "")
    ALLOWED_ORIGINS: list = [
        o.strip() for o in os.environ.get("GROUP_CHAT_CORS", "").split(",")
        if o.strip()
    ]


config = Config()


# ============================================================
# Pydantic 模型定义
# ============================================================

class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., description="用户消息内容")
    group_id: str = Field(default="default", description="群组ID")
    user_id: str = Field(default="user", description="用户ID")
    context: Optional[Dict[str, Any]] = Field(default=None, description="额外上下文")
    
    @validator("message")
    def message_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("消息不能为空")
        return v.strip()


class ChatResponse(BaseModel):
    """聊天响应"""
    status: str = Field(..., description="状态")
    reply: str = Field(..., description="AI回复")
    speaker: str = Field(..., description="发言AI")
    round_number: int = Field(..., description="当前轮次")
    quality_score: float = Field(default=0.0, description="质量分")
    message_id: str = Field(..., description="消息ID")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class MessageRequest(BaseModel):
    """消息进入群聊流请求"""
    speaker: str = Field(..., description="发言者")
    content: str = Field(..., description="消息内容")
    round_number: int = Field(..., description="轮次编号")
    speaker_type: str = Field(default="ai", description="发言者类型")
    mentions: List[str] = Field(default_factory=list, description="@提及")
    group_id: str = Field(default="default", description="群组ID")


class MessageResponse(BaseModel):
    """消息处理响应"""
    status: str
    message_id: str
    round_number: int
    action: str = ""
    next_speaker: str = ""
    announcement: str = ""


class RoundStatusResponse(BaseModel):
    """轮次状态响应"""
    round_number: int
    status: str
    topic: str
    speakers_spoken: List[str]
    message_count: int
    quality_scores: Dict[str, float]
    started_at: str
    ended_at: str = ""


class ScoresResponse(BaseModel):
    """AI质量分响应"""
    group_id: str
    scores: Dict[str, Dict[str, Any]]
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class DispatchRequest(BaseModel):
    """手动分发请求"""
    speaker: str = Field(..., description="指定发言人")
    topic: str = Field(default="", description="分配主题")
    round_number: int = Field(..., description="当前轮次")
    group_id: str = Field(default="default", description="群组ID")


class DispatchResponse(BaseModel):
    """分发响应"""
    status: str
    speaker: str
    announcement: str
    confidence: float
    reason: str = ""


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str
    version: str
    uptime: float
    qdrant_connected: bool
    active_groups: int


# ============================================================
# 全局状态管理
# ============================================================

class AppState:
    """应用状态"""
    
    def __init__(self):
        self.start_time: float = time.time()
        self.qdrant_client: Optional[Any] = None
        self.secretaries: Dict[str, SecretaryOrchestrator] = {}
        self.dispatcher: Optional[Dispatcher] = None
        self.score_calculator: Optional[ScoreCalculator] = None
        self.current_round: int = 1
        
        # 救援器（全局单例）
        self.rescuer: Optional[AIMemberRescuer] = None
        self.rescuer_ai: Optional[RescuerAI] = None
        
    def get_secretary(self, group_id: str) -> SecretaryOrchestrator:
        """获取或创建秘书长实例"""
        if group_id not in self.secretaries:
            self.secretaries[group_id] = create_secretary(
                group_id=group_id,
                client=self.qdrant_client,
                use_mock=True,
            )
        return self.secretaries[group_id]


app_state = AppState()


# ============================================================
# FastAPI 应用
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化
    logger.info("🚀 启动 AI群聊服务...")
    
    # 初始化 Qdrant 客户端
    try:
        if config.QDRANT_USE_MEMORY:
            app_state.qdrant_client = InMemoryStorage()
            logger.info("✅ 使用内存存储")
        else:
            app_state.qdrant_client = create_client(
                host=config.QDRANT_HOST,
                port=config.QDRANT_PORT,
                https=config.QDRANT_HTTPS,
                api_key=config.QDRANT_API_KEY,
                use_memory=False,
            )
            logger.info("✅ Qdrant 连接成功")
    except Exception as e:
        logger.warning(f"⚠️ Qdrant 连接失败，使用内存存储: {e}")
        app_state.qdrant_client = InMemoryStorage()
    
    # 初始化分发器
    app_state.dispatcher = Dispatcher(
        max_consecutive=config.MAX_CONSECUTIVE_SPEAKS,
    )
    
    # 初始化评分器
    app_state.score_calculator = ScoreCalculator(scorer_type="mock")
    
    # 初始化救援器（AI成员救援）
    app_state.rescuer = AIMemberRescuer()
    logger.info("✅ AI成员救援器已初始化")
    
    # 初始化救援AI（僵局调解）
    app_state.rescuer_ai = RescuerAI()
    logger.info("✅ 救援AI（僵局调解）已初始化")
    
    logger.info(f"✅ 服务启动完成，监听端口: {config.API_PORT}")
    
    yield
    
    # 关闭时清理
    logger.info("🛑 关闭 AI群聊服务...")
    if app_state.qdrant_client and hasattr(app_state.qdrant_client, "close"):
        app_state.qdrant_client.close()




# ============================================================
# 认证中间件
# ============================================================

async def verify_api_key(request: Request):
    """API Key 认证依赖。开发模式（未设 API_KEY）仅允许 localhost。"""
    import hmac
    # /health 免认证
    if request.url.path.rstrip("/") == "/health":
        return True
    if not config.API_KEY:
        # 开发模式：仅允许 localhost
        client = request.client.host if request.client else ""
        if client not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(status_code=403, detail="dev mode: localhost only")
        return True
    key = request.headers.get("X-GroupChat-Key", "")
    if not hmac.compare_digest(key, config.API_KEY):
        raise HTTPException(status_code=401, detail="unauthorized")
    return True


app = FastAPI(
    title="AI群聊服务",
    description="多AI协作群聊的 HTTP API 入口",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(verify_api_key)],
)

# CORS 中间件
# CORS: 使用配置化的 origins，不再允许通配符
_cors_origins = config.ALLOWED_ORIGINS if config.ALLOWED_ORIGINS else ["http://localhost:3000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=len(_cors_origins) > 0,
    allow_methods=["GET", "POST"],
    allow_headers=["X-GroupChat-Key", "Content-Type"],
)


# ============================================================
# 依赖注入
# ============================================================

def get_secretary(group_id: str = "default") -> SecretaryOrchestrator:
    """获取秘书长实例"""
    return app_state.get_secretary(group_id)


def get_dispatcher() -> Dispatcher:
    """获取分发器实例"""
    return app_state.dispatcher


def get_score_calculator() -> ScoreCalculator:
    """获取评分器实例"""
    return app_state.score_calculator


# ============================================================
# API 端点
# ============================================================

@app.get("/health", response_model=HealthResponse, tags=["系统"])
async def health_check():
    """健康检查"""
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        uptime=time.time() - app_state.start_time,
        qdrant_connected=app_state.qdrant_client is not None,
        active_groups=len(app_state.secretaries),
    )


@app.post("/chat", response_model=ChatResponse, tags=["聊天"])
async def chat(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
):
    """
    接收用户消息，返回 AI 回复
    
    调用链:
    1. 用户消息进入秘书长
    2. 秘书长判断是否需要 AI 回复
    3. 分发器选择发言人
    4. 调用 AI 生成回复（当前为 Mock）
    5. 评分并记录
    6. 检测僵局并触发救援AI
    7. 返回回复
    """
    logger.info(f"💬 收到聊天请求: group={request.group_id}, user={request.user_id}")
    
    secretary = get_secretary(request.group_id)
    dispatcher = get_dispatcher()
    score_calc = get_score_calculator()
    rescuer_ai = app_state.rescuer_ai
    
    # 构建消息
    message = {
        "speaker": request.user_id,
        "content": request.message,
        "round_number": app_state.current_round,
        "speaker_type": "user",
    }
    
    # 秘书长处理消息
    try:
        result = secretary.process_message(message)
        logger.info(f"✅ 消息处理完成: status={result.status}, round={result.round_number}")
    except Exception as e:
        logger.error(f"❌ 消息处理失败: {e}")
        raise HTTPException(status_code=500, detail=f"消息处理失败: {e}")
    
    # 检查是否触发新一轮（用户消息自动结束当前轮次）
    if result.status == "round_ended":
        # 检测僵局
        deadlock_check = check_deadlock(request.group_id, session_id="")
        if deadlock_check["needs_intervention"]:
            logger.warning(f"⚠️ 检测到僵局，触发救援AI: {deadlock_check['deadlock_type']}")
            if rescuer_ai:
                plan = rescuer_ai.generate_intervention("")
                logger.info(f"🆘 救援计划已生成: {plan.immediate_action}")
    
    # 选择下一发言人
    try:
        dispatch_result = secretary.select_next_speaker(topic=request.message[:50])
        next_speaker = dispatch_result.speaker or "侦查AI"
        announcement = dispatch_result.announcement
    except Exception as e:
        logger.warning(f"⚠️ 分发失败，使用默认: {e}")
        next_speaker = "侦查AI"
        announcement = f"@{next_speaker} 请发言"
    
    # 4. 生成 AI 回复
    # 使用真实的 OpenClaw Proxy
    model_mapping = {
        "侦查AI": "deepseek-v4-flash",
        "审计AI": "astron-code-latest",
        "税务AI": "deepseek-v4-flash",
        "文书AI": "astron-code-latest",
        "合规AI": "deepseek-v4-flash",
    }
    model = model_mapping.get(next_speaker, "deepseek-v4-flash")
    
    # 构建带角色的 Prompt
    role_info = AI_ROLES.get(next_speaker)
    role_prompt = f"你是{next_speaker}，擅长{role_info.specialty if role_info else '通用任务'}。请简洁回复。\n\n用户消息: {request.message}\n\n请从你的专业角度回复:"
    
    try:
        ai_reply = await _call_openclaw_proxy(role_prompt, model)
    except Exception as e:
        logger.warning(f"⚠️ AI 调用失败，降级到 Mock: {e}")
        ai_reply = _generate_mock_reply(request.message, next_speaker)
    
    # 5. 评分
    ai_message = {
        "speaker": next_speaker,
        "content": ai_reply,
        "round_number": app_state.current_round,
        "speaker_type": "ai",
    }
    score_result = score_calc.calculate_score(ai_message, topic=request.message[:50])
    
    # 6. 异步写入 AI 消息
    background_tasks.add_task(
        _write_ai_message,
        secretary,
        ai_message,
    )
    
    # 7. 自动结束当前轮次，开始新一轮
    # 每轮 = 1条用户消息 → 1个AI回复，结束后自动递增轮次编号
    try:
        secretary.end_round(app_state.current_round)
        app_state.current_round += 1
        logger.info(f"🏁 轮次 {app_state.current_round - 1} 结束，进入轮次 {app_state.current_round}")
    except Exception as e:
        logger.warning(f"⚠️ 自动结束轮次失败: {e}")
    
    return ChatResponse(
        status="success",
        reply=ai_reply,
        speaker=next_speaker,
        round_number=app_state.current_round - 1,
        quality_score=score_result.total_score,
        message_id=result.message_id,
    )


@app.post("/message", response_model=MessageResponse, tags=["消息"])
async def post_message(
    request: MessageRequest,
    background_tasks: BackgroundTasks,
):
    """
    消息进入群聊流（秘书长处理）
    
    用于 AI 发言或系统消息进入群聊流程。
    """
    logger.info(f"📨 收到消息: speaker={request.speaker}, round={request.round_number}")
    
    secretary = get_secretary(request.group_id)
    
    # 构建消息
    message = {
        "speaker": request.speaker,
        "content": request.content,
        "round_number": request.round_number,
        "speaker_type": request.speaker_type,
        "mentions": request.mentions,
    }
    
    # 秘书长处理
    try:
        result = secretary.process_message(message)
        logger.info(f"✅ 消息处理完成: status={result.status}")
    except Exception as e:
        logger.error(f"❌ 消息处理失败: {e}")
        raise HTTPException(status_code=500, detail=f"消息处理失败: {e}")
    
    return MessageResponse(
        status=result.status,
        message_id=result.message_id,
        round_number=result.round_number,
        action=result.action,
        next_speaker=result.next_speaker,
        announcement=result.announcement,
    )


@app.get("/round/group/{gid}", response_model=RoundStatusResponse, tags=["轮次"])
async def get_latest_round_by_group(
    gid: str,
):
    """
    通过 group_id 查询最新轮次状态
    
    备选端点，接受任意字符串 group_id。
    """
    return await get_round_status(round_id="current", group_id=gid)


@app.get("/round/{round_id}", response_model=RoundStatusResponse, tags=["轮次"])
async def get_round_status(
    round_id: str,
    group_id: str = "default",
):
    """
    查询轮次状态
    
    支持 numeric 和 string 类型的 round_id。
    若传 "current" 或 "latest"，返回当前活跃轮次。
    """
    logger.info(f"📊 查询轮次状态: round={round_id}, group={group_id}")
    
    secretary = get_secretary(group_id)
    state = secretary.get_current_state()
    
    current_round = state.get("current_round")
    
    # 解析 round_id："current"/"latest"/数字字符串→int
    if round_id.lower() in ("current", "latest"):
        # 返回当前轮次（活跃中，或最近结束的）
        if current_round:
            round_number = current_round.get("round_number", 1)
        else:
            round_number = app_state.current_round
    else:
        try:
            round_number = int(round_id)
        except ValueError:
            # 提供 /round/group/{group_id} 备选端点
            raise HTTPException(
                status_code=400,
                detail=f"round_id 必须是整数或 'current'/'latest'，收到: {round_id}",
            )
    
    if not current_round or current_round.get("round_number") != round_number:
        raise HTTPException(status_code=404, detail=f"轮次 {round_number} 不存在")
    
    return RoundStatusResponse(
        round_number=round_number,
        status=current_round.get("status", "unknown"),
        topic=current_round.get("topic", ""),
        speakers_spoken=current_round.get("speakers_spoken", []),
        message_count=current_round.get("message_count", 0),
        quality_scores=state.get("speaker_states", {}),
        started_at=current_round.get("started_at", ""),
        ended_at=current_round.get("ended_at", ""),
    )


@app.get("/scores", response_model=ScoresResponse, tags=["评分"])
async def get_scores(group_id: str = "default"):
    """
    查询 AI 质量分
    
    返回所有 AI 的当前质量评分。
    """
    logger.info(f"📊 查询质量分: group={group_id}")
    
    secretary = get_secretary(group_id)
    state = secretary.get_current_state()
    
    scores = {}
    for speaker, s_state in state.get("speaker_states", {}).items():
        scores[speaker] = {
            "current_score": s_state.get("current_score", 0.0),
            "deprivation_level": s_state.get("deprivation_level", 0),
            "total_messages": s_state.get("total_messages", 0),
            "last_active_round": s_state.get("last_active_round", 0),
        }
    
    # 补充所有 AI 角色
    for ai_name in AI_ROLES.keys():
        if ai_name not in scores:
            scores[ai_name] = {
                "current_score": 0.5,
                "deprivation_level": 0,
                "total_messages": 0,
                "last_active_round": 0,
            }
    
    return ScoresResponse(
        group_id=group_id,
        scores=scores,
    )


@app.post("/dispatch", response_model=DispatchResponse, tags=["分发"])
async def manual_dispatch(request: DispatchRequest):
    """
    手动 @ 指定 AI
    
    强制指定某个 AI 发言。
    """
    logger.info(f"🎯 手动分发: speaker={request.speaker}, round={request.round_number}")
    
    secretary = get_secretary(request.group_id)
    dispatcher = get_dispatcher()
    
    # 验证 AI 是否存在
    if request.speaker not in AI_ROLES:
        raise HTTPException(status_code=400, detail=f"未知的 AI: {request.speaker}")
    
    # 生成分发指令
    result = dispatcher.assign_topic(
        speaker=request.speaker,
        topic=request.topic,
        context={"round_number": request.round_number},
    )
    
    return DispatchResponse(
        status="success",
        speaker=request.speaker,
        announcement=result["announcement"],
        confidence=result["confidence"],
        reason=result["reason"],
    )


@app.get("/ais", tags=["系统"])
async def list_ais():
    """
    列出所有 AI 角色
    
    返回所有可用的 AI 及其专业领域。
    """
    return {
        "ais": [
            {
                "name": role.name,
                "specialty": role.specialty,
                "topics": role.topics,
                "color": role.color,
            }
            for role in AI_ROLES.values()
        ]
    }


@app.post("/round/end", tags=["轮次"])
async def end_round(
    round_number: int,
    group_id: str = "default",
    force: bool = False,
):
    """
    结束当前轮次
    
    生成摘要并更新评分。
    """
    logger.info(f"🏁 结束轮次: round={round_number}, group={group_id}")
    
    secretary = get_secretary(group_id)
    
    try:
        result = secretary.end_round(round_number, force=force)
        app_state.current_round += 1
        logger.info(f"✅ 轮次结束: {result}")
        return result
    except Exception as e:
        logger.error(f"❌ 结束轮次失败: {e}")
        raise HTTPException(status_code=500, detail=f"结束轮次失败: {e}")


@app.get("/deadlock/check", response_model=Dict[str, Any], tags=["救援AI"])
async def check_deadlock(
    group_id: str = "default",
    session_id: str = "",
):
    """
    检测僵局状态
    
    Args:
        group_id: 群组ID
        session_id: 会话ID（可选）
    
    Returns:
        包含僵局检测结果的字典
    """
    logger.info(f"🔍 检测僵局: group={group_id}, session={session_id}")
    
    rescuer_ai = app_state.rescuer_ai
    if not rescuer_ai:
        return {
            "status": "error",
            "message": "救援AI未初始化",
            "needs_intervention": False
        }
    
    # 快速检查
    quick_result = rescuer_ai.quick_check(session_id or f"{group_id}_default")
    
    return {
        "status": "ok",
        "needs_intervention": quick_result["needs_rescue"],
        "deadlock_type": quick_result.get("deadlock_type"),
        "severity": quick_result.get("severity", 0),
        "action_required": quick_result["needs_rescue"]
    }


@app.post("/rescue/trigger", response_model=Dict[str, Any], tags=["救援AI"])
async def trigger_rescue(
    group_id: str = "default",
    session_id: str = "",
    intervention_type: str = "question",
):
    """
    触发救援介入
    
    Args:
        group_id: 群组ID
        session_id: 会话ID
        intervention_type: 介入类型 (question/summary/proposal/mediation/energize)
    
    Returns:
        救援计划结果
    """
    logger.info(f"🚨 触发救援: group={group_id}, session={session_id}, type={intervention_type}")
    
    rescuer_ai = app_state.rescuer_ai
    if not rescuer_ai:
        return {
            "status": "error",
            "message": "救援AI未初始化"
        }
    
    try:
        # 生成救援计划
        plan = rescuer_ai.generate_intervention(session_id or f"{group_id}_default")
        
        return {
            "status": "success",
            "plan": plan.to_dict(),
            "immediate_action": plan.immediate_action,
            "monitoring_points": plan.monitoring_points
        }
    except Exception as e:
        logger.error(f"❌ 救援失败: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


# ============================================================
# WebSocket 端点
# ============================================================

# 活跃 WebSocket 连接池：{group_id: [WebSocket, ...]}
_ws_connections: Dict[str, List[WebSocket]] = {}
_ws_broadcast_tasks: Dict[str, asyncio.Task] = {}


async def _broadcast_round_status(group_id: str):
    """
    每秒向指定 group 的所有 WebSocket 客户端推送轮次状态+评分。
    """
    import copy

    while True:
        try:
            await asyncio.sleep(1.0)

            if group_id not in _ws_connections or not _ws_connections[group_id]:
                # 没有客户端了，停止广播
                break

            secretary = get_secretary(group_id)
            state = secretary.get_current_state()

            current_round = state.get("current_round", {})
            speaker_states = state.get("speaker_states", {})

            # 构建推送数据（序列化安全，淘掉不可序列化的对象）
            payload = {
                "type": "round_status",
                "timestamp": datetime.now().isoformat(),
                "round_number": current_round.get("round_number", app_state.current_round),
                "round_status": current_round.get("status", "unknown"),
                "topic": current_round.get("topic", ""),
                "speakers_spoken": current_round.get("speakers_spoken", []),
                "message_count": current_round.get("message_count", 0),
                "started_at": current_round.get("started_at", ""),
                "ended_at": current_round.get("ended_at", ""),
                "quality_scores": {
                    k: {
                        "current_score": float(v.get("current_score", 0.5)) if isinstance(v, dict) else 0.5,
                        "deprivation_level": int(v.get("deprivation_level", 0)) if isinstance(v, dict) else 0,
                    }
                    for k, v in speaker_states.items()
                },
                "all_speakers": list(AI_ROLES.keys()),
            }

            # 向所有客户端推送
            dead_conns = []
            for ws in _ws_connections.get(group_id, []):
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead_conns.append(ws)

            # 清理断开的连接
            for d in dead_conns:
                try:
                    _ws_connections[group_id].remove(d)
                except (ValueError, KeyError):
                    pass

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"❌ WS广播异常 [{group_id}]: {e}")
            await asyncio.sleep(1.0)


@app.websocket("/ws/round/{group_id}")
async def websocket_round_status(websocket: WebSocket, group_id: str = "default"):
    """
    WebSocket 端点：每秒推送轮次状态+评分数据。
    
    用法:
        ws = new WebSocket("ws://localhost:18890/ws/round/default")
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            // data.type === "round_status"
            // data.round_number, data.round_status, data.topic, data.quality_scores, ...
        };
    """
    await websocket.accept()
    logger.info(f"🔌 WebSocket 已连接: group={group_id}")

    # 注册到连接池
    if group_id not in _ws_connections:
        _ws_connections[group_id] = []
    _ws_connections[group_id].append(websocket)

    # 启动广播任务（如果还没有）
    if group_id not in _ws_broadcast_tasks or _ws_broadcast_tasks[group_id].done():
        _ws_broadcast_tasks[group_id] = asyncio.create_task(
            _broadcast_round_status(group_id)
        )

    try:
        # 保持连接，等待断开
        while True:
            # 接收客户端消息（用于保持心跳/接收命令）
            data = await websocket.receive_text()
            logger.debug(f"🔌 WS 收到消息 [{group_id}]: {data}")
    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket 断开: group={group_id}")
    except Exception as e:
        logger.error(f"🔌 WebSocket 异常 [{group_id}]: {e}")
    finally:
        # 从连接池移除
        if group_id in _ws_connections:
            try:
                _ws_connections[group_id].remove(websocket)
            except (ValueError, KeyError):
                pass
            # 如果没有客户端了，取消广播任务
            if not _ws_connections[group_id] and group_id in _ws_broadcast_tasks:
                _ws_broadcast_tasks[group_id].cancel()
                del _ws_broadcast_tasks[group_id]


# ============================================================
# 内部辅助函数
# ============================================================

async def _call_openclaw_proxy(message: str, model: str = "deepseek-v4-flash") -> str:
    """
    调用真实的 OpenClaw Proxy
    
    Args:
        message: 用户消息
        model: 模型名称
    
    Returns:
        AI 回复文本
    """
    import httpx
    
    url = f"{config.OPENCLAW_PROXY_URL}/chat/completions"
    
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": message}
        ],
        "temperature": 0.7,
        "max_tokens": 1000,
    }
    
    headers = {}
    if config.OPENCLAW_API_KEY:
        headers["Authorization"] = f"Bearer {config.OPENCLAW_API_KEY}"
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"❌ OpenClaw 调用失败: {e}")
        # 降级到 Mock
        return f"[降级回复] AI 服务暂时不可用，原因为: {str(e)}"


def _generate_mock_reply(user_message: str, speaker: str) -> str:
    """
    生成 Mock 回复（降级使用）
    """
    role = AI_ROLES.get(speaker)
    specialty = role.specialty if role else "通用助手"
    
    # 简单的 Mock 回复
    replies = {
        "侦查AI": f"我已查询相关信息。根据公开数据，{user_message[:30]}... 的相关情况如下：[Mock数据]",
        "审计AI": f"审计分析完成。针对 {user_message[:30]}...，发现以下要点：[Mock分析]",
        "税务AI": f"税务核验结果：{user_message[:30]}... 的税务信息已确认。[Mock结果]",
        "文书AI": f"已起草相关文书。针对 {user_message[:30]}...，建议格式如下：[Mock文书]",
        "合规AI": f"合规审查完成。{user_message[:30]}... 的合规性分析如下：[Mock审查]",
    }
    
    return replies.get(speaker, f"[{speaker}] 收到您的消息，正在处理中。")


async def _write_ai_message(
    secretary: SecretaryOrchestrator,
    message: Dict[str, Any],
):
    """
    异步写入 AI 消息
    """
    try:
        result = secretary.process_message(message)
        logger.debug(f"✅ AI消息写入成功: {result.message_id}")
    except Exception as e:
        logger.error(f"❌ AI消息写入失败: {e}")


# ============================================================
# 错误处理
# ============================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTP 异常处理"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "code": exc.status_code,
            "message": exc.detail,
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """通用异常处理"""
    logger.error(f"❌ 未处理异常: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "code": 500,
            "message": "内部服务器错误",
        },
    )


# ============================================================
# 启动入口
# ============================================================

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "group_chat_api:app",
        host="0.0.0.0",
        port=config.API_PORT,
        reload=config.DEBUG,
        log_level="info",
    )
