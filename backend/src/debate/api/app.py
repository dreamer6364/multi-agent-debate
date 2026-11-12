"""应用装配（create_app）：配置/DB/引擎依赖注入点，供生产与测试复用（ARCH §2）。"""
from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlmodel import select

from ..core.config import AppConfig, deep_merge, load_config, load_env
from ..db.models import DebateRow, TopicRow
from ..db.session import init_db, make_engine, make_sessionmaker
from ..engine.agents.loader import load_agents, load_topics
from ..engine.llm.gateway import Gateway, extra_body_from_env
from ..engine.schema import DebateFormat
from ..services.cache import build_cache
from ..services.debate_service import DebateService
from .auth import router as auth_router
from .debates import router as debates_router
from .deps import AppState, get_session
from .errors import register_error_handlers
from .topics import router as topics_router

_TZ = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger("debate.api")


def _default_now() -> datetime:
    return datetime.now(_TZ).replace(tzinfo=None)


def _default_gateway_factory(config: AppConfig, fmt: DebateFormat) -> Gateway:
    del fmt  # 真实网关不分赛制；服务层契约统一为 (config, fmt)
    return Gateway(
        model_debater=os.environ.get("MODEL_DEBATER", ""),
        model_official=os.environ.get("MODEL_OFFICIAL", ""),
        max_retries=config.model.max_retries,
        min_interval=float(os.environ.get("MIN_CALL_INTERVAL", config.model.min_call_interval)),
        extra_body=extra_body_from_env(),
    )


def _default_compressor_factory(config: AppConfig):
    from ..engine.llm.compressor import make_llm_columns_compressor

    def build(cfg: AppConfig, gateway: Gateway):
        return make_llm_columns_compressor(gateway, temperature=cfg.model.temperature.digest)

    return build


async def _seed_builtin_topics(state: AppState) -> None:
    """内置辩题入库（幂等，按标题去重）。"""
    async with state.sessionmaker() as session:
        for topic in load_topics(Path(state.assets_root)):
            exists = await session.execute(
                select(TopicRow).where(TopicRow.is_builtin.is_(True), TopicRow.title == topic.title)
            )
            if exists.scalar_one_or_none() is None:
                session.add(TopicRow(
                    title=topic.title, pro_stance=topic.pro_stance, con_stance=topic.con_stance,
                    pro_points="\n".join(topic.pro_points), con_points="\n".join(topic.con_points),
                    is_builtin=True, owner_id=None,
                ))
        await session.commit()
    # 内置辩题出现在所有用户的列表里：播种后按前缀全量失效（Redis 跨重启持久，
    # 只靠 TTL 会让新增内置辩题最长延迟一个 TTL 才可见）
    if state.cache is not None:
        await state.cache.delete_prefix("mad:topics:")


def create_app(
    *,
    config_path: str | Path | None = None,
    db_url: str | None = None,
    jwt_secret: str | None = None,
    gateway_factory=None,
    compressor_factory=None,
    config_overrides: dict | None = None,
    now_fn=None,
    heartbeat_seconds: float | None = None,
    cache=None,
) -> FastAPI:
    config_file = Path(config_path or os.environ.get("CONFIG_PATH", "config.yaml"))
    config = load_config(config_file)
    load_env(config_file.parent / ".env")  # override=False：显式环境变量优先
    if config_overrides:
        config = AppConfig.model_validate(deep_merge(config.model_dump(), config_overrides))

    # ISS-008：CORS_ORIGINS 此前是死配置（.env 提供但从未挂中间件）。逗号分隔白名单；
    # 空 = 不挂中间件（同源/vite proxy 部署，与开发约定一致）。
    cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]

    # ISS-009：密钥硬化——不再回落内置公开值 dev-insecure-secret（知道默认值即可伪造任意用户会话）。
    # 生产档（DEBATE_ENV=prod）缺 JWT_SECRET 直接拒绝启动；开发档改用每次启动随机密钥并告警
    # （重启后会话失效，比固定公开值安全）。显式传入的 jwt_secret（测试注入）永远优先。
    secret = jwt_secret or os.environ.get("JWT_SECRET")
    if not secret:
        if os.environ.get("DEBATE_ENV", "dev").lower() in ("prod", "production"):
            raise RuntimeError("DEBATE_ENV=prod 时必须设置 JWT_SECRET，拒绝以默认密钥启动")
        secret = secrets.token_urlsafe(32)
        logger.warning(
            "JWT_SECRET 未设置：本次启动使用随机密钥，重启后所有登录会话失效（生产请显式配置）"
        )
    expire_minutes = int(os.environ.get("JWT_EXPIRE_MINUTES", "1440"))
    engine = make_engine(db_url, base_dir=config_file.parent)
    sessionmaker = make_sessionmaker(engine)
    agents = load_agents(config_file.parent)
    now = now_fn or _default_now
    service = DebateService(
        config=config, agents=agents, sessionmaker=sessionmaker,
        gateway_factory=gateway_factory or _default_gateway_factory,
        compressor_factory=compressor_factory or _default_compressor_factory(config),
        now_fn=now,
    )
    state = AppState(
        config=config, assets_root=config_file.parent, jwt_secret=secret,
        jwt_expire_minutes=expire_minutes, sessionmaker=sessionmaker, engine=engine,
        service=service, gateway_factory=gateway_factory or _default_gateway_factory,
        compressor_factory=compressor_factory or _default_compressor_factory(config),
        now_fn=now, cookie_secure=os.environ.get("COOKIE_SECURE") == "1",
        sse_heartbeat=heartbeat_seconds if heartbeat_seconds is not None
        else float(os.environ.get("SSE_HEARTBEAT_SECONDS", "15")),
        # 只读旁路缓存：显式注入（测试）优先，否则按 REDIS_URL 构建（未配置回落进程内）
        cache=cache if cache is not None else build_cache(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await init_db(engine)
        await _seed_builtin_topics(state)
        # 启动清扫（ARCH §15）：重启后僵尸 pending/running 任务标记 failed
        async with sessionmaker() as sweep:
            stale = (await sweep.execute(
                select(DebateRow).where(DebateRow.status.in_(["pending", "running"]))
            )).scalars().all()
            for row in stale:
                row.status = "failed"
                row.error = "服务重启导致比赛中断"
            if stale:
                await sweep.commit()
        yield
        await service.shutdown()
        await engine.dispose()
        if state.cache is not None:
            await state.cache.aclose()

    app = FastAPI(title="Multi-Agent Debate API", version="0.1.0", lifespan=lifespan)
    if cors_origins:
        # 仅白名单 origin 放行且允许携带凭据（Cookie 为 httpOnly；禁止通配符 + credentials 组合）
        from starlette.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )
    app.state.svc = state
    register_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(topics_router)
    app.include_router(debates_router)

    @app.get("/api/health")
    async def health(session=Depends(get_session)):
        await session.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}

    return app
