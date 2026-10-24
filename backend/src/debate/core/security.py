"""认证安全（SPEC §9）：bcrypt 哈希 + JWT（httpOnly Cookie 承载，ARCH A3）。"""
from __future__ import annotations

import bcrypt
import jwt

_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: int, *, secret: str, expire_minutes: int, now=None) -> str:
    import time

    payload = {"sub": str(user_id), "iat": int(now() if now else time.time()), "exp": int((now() if now else time.time()) + expire_minutes * 60)}
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def decode_token(token: str, *, secret: str) -> int | None:
    """解码并返回 user_id；无效/过期返回 None。"""
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
