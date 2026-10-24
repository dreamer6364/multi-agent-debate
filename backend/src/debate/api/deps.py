"""依赖注入：会话、当前用户（httpOnly Cookie JWT）、应用状态。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import AppConfig
from ..core.security import decode_token
from ..db.models import User
from ..services.debate_service import DebateService
from .errors import ApiError

COOKIE_NAME = "access_token"


@dataclass
class AppState:
    config: AppConfig
    assets_root: object
    jwt_secret: str
    jwt_expire_minutes: int
    sessionmaker: object
    engine: object
    service: DebateService
    gateway_factory: Callable
    compressor_factory: Callable
    now_fn: Callable
    cookie_secure: bool = False
    sse_heartbeat: float = 15.0
    extras: dict = field(default_factory=dict)


def get_state(request: Request) -> AppState:
    return request.app.state.svc


async def get_session(request: Request) -> AsyncSession:
    state: AppState = request.app.state.svc
    async with state.sessionmaker() as session:
        yield session


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    state: AppState = request.app.state.svc
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise ApiError(401, "AUTH_REQUIRED", "未登录或会话已过期")
    user_id = decode_token(token, secret=state.jwt_secret)
    if user_id is None:
        raise ApiError(401, "AUTH_REQUIRED", "未登录或会话已过期")
    user = await session.get(User, user_id)
    if user is None:
        raise ApiError(401, "AUTH_REQUIRED", "用户不存在")
    return user
