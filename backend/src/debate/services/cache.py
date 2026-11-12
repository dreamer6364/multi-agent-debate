"""只读旁路缓存（cache-aside）。

约束口径（CONSTRAINTS B5/B8 修订后）：DB 是唯一事实来源，缓存只作读加速，
不承载任何状态语义；EventBus 仍为进程内实现，Redis 仅作可选缓存层。

设计要点：
- 读：先查缓存，miss 回源 DB 并回填；写：写路径主动失效 + TTL 兜底双保险
  （主动失效覆盖本应用内的写，TTL 兜底覆盖漏失效与进程外改动）。
- 降级链：REDIS_URL 未配置 → 进程内 MemoryCache（零配置开发/测试，单实例语义下
  与 Redis 等价）；Redis 不可达或运行中故障 → 告警后按 miss 处理直读 DB，
  缓存故障绝不阻断请求（与联网搜索回落本地证据卡同一哲学）。
- redis 客户端懒连接（from_url 不建连），因此 create_app 同步路径即可构造，
  连接/故障都收敛在各操作的 try/except 内。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Protocol

logger = logging.getLogger("debate.cache")

KEY_PREFIX = "mad"  # 键命名空间，避免与同库其他应用撞键


class CacheBackend(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl: int) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def delete_prefix(self, prefix: str) -> None: ...

    async def aclose(self) -> None: ...


class MemoryCache:
    """进程内缓存：REDIS_URL 缺省时的回落实现（惰性过期，无淘汰策略）。"""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float]] = {}

    async def get(self, key: str) -> str | None:
        hit = self._store.get(key)
        if hit is None:
            return None
        value, expire_at = hit
        if time.monotonic() >= expire_at:
            self._store.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, ttl: int) -> None:
        self._store[key] = (value, time.monotonic() + ttl)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def delete_prefix(self, prefix: str) -> None:
        for key in [k for k in self._store if k.startswith(prefix)]:
            self._store.pop(key, None)

    async def aclose(self) -> None:
        self._store.clear()


class RedisCache:
    """Redis 缓存：所有操作失败降级为 miss/空操作，绝不向上抛（可用性优先）。"""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._warned = False

    def _degrade(self, action: str, exc: Exception) -> None:
        # 首次故障告警一次，避免每个请求刷屏；后续按 debug 记录
        if not self._warned:
            self._warned = True
            logger.warning(
                "Redis %s 失败，降级为直读 DB（缓存暂不可用，不影响请求）: %s", action, exc
            )
        else:
            logger.debug("Redis %s 失败: %s", action, exc)

    async def get(self, key: str) -> str | None:
        try:
            value = await self._client.get(key)
        except Exception as exc:  # 降级边界：任何 Redis 故障都不得中断请求
            self._degrade("get", exc)
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    async def set(self, key: str, value: str, ttl: int) -> None:
        try:
            await self._client.set(key, value, ex=ttl)
        except Exception as exc:
            self._degrade("set", exc)

    async def delete(self, key: str) -> None:
        try:
            await self._client.delete(key)
        except Exception as exc:
            self._degrade("delete", exc)

    async def delete_prefix(self, prefix: str) -> None:
        try:
            async for key in self._client.scan_iter(match=f"{prefix}*"):
                await self._client.delete(key)
        except Exception as exc:
            self._degrade("delete_prefix", exc)

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception as exc:
            self._degrade("aclose", exc)


def build_cache() -> CacheBackend:
    """按 REDIS_URL 构建缓存后端；未配置回落进程内缓存（零配置不变）。

    注意：不在这里 ping——redis 客户端懒连接，若 REDIS_URL 指向的实例不可达，
    首次操作会在 RedisCache 内降级并告警，启动不受影响。
    """
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        return MemoryCache()
    try:
        import redis.asyncio as aioredis

        return RedisCache(aioredis.from_url(url, socket_connect_timeout=2))
    except Exception as exc:  # 构造失败同样回落，不阻断应用启动
        logger.warning("Redis 客户端构造失败，回落进程内缓存: %s", exc)
        return MemoryCache()


def cache_key(*parts: object) -> str:
    """统一键构造：{KEY_PREFIX}:{用途}:{id}。"""
    return ":".join([KEY_PREFIX, *(str(p) for p in parts)])


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def loads(raw: str | None) -> Any | None:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):  # 脏数据视同 miss，回源重建
        return None
