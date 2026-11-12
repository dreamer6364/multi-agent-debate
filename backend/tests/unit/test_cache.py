"""缓存模块单测（services/cache.py）：TTL/失效语义、Redis 降级链、键构造。

Redis 交互用 fakeredis（进程内假服务器，不依赖本机 redis-server）；
降级用故意抛错的 stub 验证"缓存故障绝不中断请求"。
"""
from __future__ import annotations

from fakeredis import aioredis as fake_aioredis

from debate.services.cache import (
    MemoryCache,
    RedisCache,
    build_cache,
    cache_key,
    dumps,
    loads,
)


async def test_memory_cache_roundtrip_and_delete():
    cache = MemoryCache()
    assert await cache.get("k") is None
    await cache.set("k", "v", ttl=60)
    assert await cache.get("k") == "v"
    await cache.delete("k")
    assert await cache.get("k") is None


async def test_memory_cache_ttl_expiry():
    cache = MemoryCache()
    await cache.set("k", "v", ttl=0)  # 立即过期
    assert await cache.get("k") is None


async def test_memory_cache_delete_prefix_scopes_correctly():
    cache = MemoryCache()
    await cache.set("mad:topics:list:1", "a", ttl=60)
    await cache.set("mad:topics:list:2", "b", ttl=60)
    await cache.set("mad:other", "c", ttl=60)
    await cache.delete_prefix("mad:topics:")
    assert await cache.get("mad:topics:list:1") is None
    assert await cache.get("mad:topics:list:2") is None
    assert await cache.get("mad:other") == "c"  # 前缀外的键不受影响


async def test_redis_cache_roundtrip_with_fakeredis():
    client = fake_aioredis.FakeRedis()
    cache = RedisCache(client)
    await cache.set("k", "value-json", ttl=60)
    assert await cache.get("k") == "value-json"
    await cache.delete("k")
    assert await cache.get("k") is None
    await cache.aclose()


async def test_redis_cache_degrades_to_miss_on_error():
    """Redis 故障时 get 返回 None（视同 miss 回源 DB），set/delete 静默吞掉——请求不中断。"""

    class BrokenClient:
        async def get(self, key):
            raise ConnectionError("connection refused")

        async def set(self, key, value, ex=None):
            raise ConnectionError("connection refused")

        async def delete(self, key):
            raise ConnectionError("connection refused")

    cache = RedisCache(BrokenClient())
    assert await cache.get("k") is None
    await cache.set("k", "v", ttl=60)  # 不抛
    await cache.delete("k")  # 不抛
    await cache.delete_prefix("mad:")  # 不抛
    await cache.aclose()  # 不抛


def test_build_cache_falls_back_to_memory_without_redis_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert isinstance(build_cache(), MemoryCache)


def test_build_cache_uses_redis_when_url_configured(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    backend = build_cache()
    assert isinstance(backend, RedisCache)


def test_cache_key_and_json_helpers():
    assert cache_key("topics", "list", 42) == "mad:topics:list:42"
    payload = {"items": [{"title": "中文 / 不转义"}]}
    assert loads(dumps(payload)) == payload
    assert loads(None) is None
    assert loads("not-json{") is None  # 脏数据视同 miss
