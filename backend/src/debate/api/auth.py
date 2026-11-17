"""认证端点（SPEC §5.2 / ARCH A3）：注册、登录（httpOnly Cookie）、登出、me。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..core.security import create_access_token, hash_password, verify_password
from ..db.models import User, iso_cn
from .deps import COOKIE_NAME, AppState, get_current_user, get_session, get_state
from .errors import ApiError

router = APIRouter(prefix="/api/auth", tags=["auth"])

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


class RegisterIn(BaseModel):
    username: str
    password: str = Field(min_length=8)


class LoginIn(BaseModel):
    username: str
    password: str


def _set_cookie(response: Response, state: AppState, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME, value=token, httponly=True, samesite="lax", path="/",
        max_age=state.jwt_expire_minutes * 60, secure=state.cookie_secure,
    )


@router.post("/register", status_code=201)
async def register(body: RegisterIn, session: AsyncSession = Depends(get_session)):
    if not _USERNAME_RE.match(body.username):
        raise ApiError(422, "VALIDATION_ERROR", "用户名须为 3~32 位字母数字下划线")
    existing = (await session.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if existing is not None:
        raise ApiError(409, "USERNAME_TAKEN", "用户名已被占用")
    user = User(username=body.username, password_hash=hash_password(body.password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return {"id": user.id, "username": user.username, "created_at": iso_cn(user.created_at)}


@router.post("/login")
async def login(body: LoginIn, response: Response, request: Request,
                session: AsyncSession = Depends(get_session)):
    state: AppState = get_state(request)
    guard = state.login_guard
    guard.check(body.username)  # 锁定中 → 429，不查库不跑 bcrypt（ISS-014 C-1）
    user = (await session.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        guard.record_failure(body.username)
        raise ApiError(401, "AUTH_INVALID", "用户名或密码错误")
    guard.record_success(body.username)
    token = create_access_token(user.id, secret=state.jwt_secret,
                                expire_minutes=state.jwt_expire_minutes)
    _set_cookie(response, state, token)
    return {"id": user.id, "username": user.username}


@router.post("/logout", status_code=204)
async def logout(response: Response):
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return None


@router.get("/me")
async def me(request: Request, user: User = Depends(get_current_user)):
    state: AppState = get_state(request)
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "quota": {"concurrent_limit": state.config.quota.concurrent_debates_per_user, "daily_limit": None},
    }
