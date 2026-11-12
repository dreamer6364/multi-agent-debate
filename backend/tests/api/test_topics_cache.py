"""辩题列表缓存集成测试（cache-aside：命中回填、写路径失效、按用户隔离）。

验证口径：DB 是唯一事实来源——缓存只影响性能，任何写路径（新建/删除/自建辩题
随场次落库）之后，下一次 GET 必须回源看到最新数据，绝不允许 TTL 内的脏读。
"""
from __future__ import annotations

from debate.services.cache import cache_key

from .conftest import new_client, register_and_login, wait_status


def _list_key(user_id: int) -> str:
    return cache_key("topics", "list", user_id)


async def test_topic_list_cached_then_invalidated_on_create(env):
    client = await new_client(env)
    login = await register_and_login(client, "alice")
    cache = env.app.state.svc.cache

    first = await client.get("/api/topics")
    assert first.status_code == 200
    # 首次 GET 回源并回填缓存
    assert await cache.get(_list_key(login["id"])) is not None

    # 写路径必须立即失效（不等 TTL）
    created = await client.post("/api/topics", json={
        "title": "缓存失效验证 / 甲乙", "pro_points": "要点", "con_points": "要点"})
    assert created.status_code == 201
    assert await cache.get(_list_key(login["id"])) is None, "新建辩题后列表缓存未失效"

    second = await client.get("/api/topics")
    titles = [t["title"] for t in second.json()["items"]]
    assert "缓存失效验证 / 甲乙" in titles, "失效后 GET 未回源，读到脏缓存"
    await client.aclose()


async def test_topic_list_cached_then_invalidated_on_delete(env):
    client = await new_client(env)
    login = await register_and_login(client, "alice")
    cache = env.app.state.svc.cache

    created = await client.post("/api/topics", json={
        "title": "删除即失效 / 甲乙", "pro_points": "p", "con_points": "c"})
    topic_id = created.json()["id"]
    await client.get("/api/topics")  # 回填

    deleted = await client.delete(f"/api/topics/{topic_id}")
    assert deleted.status_code == 204
    assert await cache.get(_list_key(login["id"])) is None, "删除辩题后列表缓存未失效"

    titles = [t["title"] for t in (await client.get("/api/topics")).json()["items"]]
    assert "删除即失效 / 甲乙" not in titles
    await client.aclose()


async def test_topic_list_cache_isolated_per_user(env):
    """缓存键按用户隔离：alice 的列表（含自建）绝不能串给 bob（与 IDOR 边界一致）。"""
    alice, bob = await new_client(env), await new_client(env)
    alice_login = await register_and_login(alice, "alice")
    bob_login = await register_and_login(bob, "bob")
    cache = env.app.state.svc.cache

    await alice.post("/api/topics", json={
        "title": "alice 专属辩题 / 甲乙", "pro_points": "p", "con_points": "c"})
    await alice.get("/api/topics")  # alice 侧回填
    await bob.get("/api/topics")    # bob 侧回填

    alice_items = [t["title"] for t in (await alice.get("/api/topics")).json()["items"]]
    bob_items = [t["title"] for t in (await bob.get("/api/topics")).json()["items"]]
    assert "alice 专属辩题 / 甲乙" in alice_items
    assert "alice 专属辩题 / 甲乙" not in bob_items

    # 两把键独立存在
    assert await cache.get(_list_key(alice_login["id"])) is not None
    assert await cache.get(_list_key(bob_login["id"])) is not None
    await alice.aclose()
    await bob.aclose()


async def test_custom_topic_via_debate_creation_invalidates_cache(env):
    """自建辩题随场次同事务落库（ISS-002）：该路径同样必须使列表缓存失效。"""
    client = await new_client(env)
    login = await register_and_login(client, "alice")
    cache = env.app.state.svc.cache
    await client.get("/api/topics")  # 先回填，制造"将变脏"的前提

    created = await client.post("/api/debates", json={
        "custom_topic": {"title": "随场次落库的辩题 / 甲乙",
                         "pro_points": "p", "con_points": "c"},
        "format": "lite"})
    assert created.status_code == 202, created.text
    assert await cache.get(_list_key(login["id"])) is None, "自建辩题路径未失效列表缓存"

    titles = [t["title"] for t in (await client.get("/api/topics")).json()["items"]]
    assert "随场次落库的辩题 / 甲乙" in titles

    # 收尾：终止场次，避免跨用例残留任务
    debate_id = created.json()["id"]
    await client.post(f"/api/debates/{debate_id}/stop")
    await wait_status(client, debate_id, "stopped")
    await client.aclose()


async def test_degrade_to_db_when_cache_unusable(env):
    """Redis 故障时 GET 仍 200（RedisCache 内部降级直读 DB），写路径失效也不得抛错。"""
    client = await new_client(env)
    await register_and_login(client, "alice")

    class BrokenClient:
        async def get(self, key):
            raise ConnectionError("redis down")

        async def set(self, key, value, ex=None):
            raise ConnectionError("redis down")

        async def delete(self, key):
            raise ConnectionError("redis down")

        async def scan_iter(self, match=None):
            raise ConnectionError("redis down")  # 异步生成器桩：调用即抛，落入降级分支

        async def aclose(self):
            raise ConnectionError("redis down")

    # 注入真实的 RedisCache 包裹故障客户端：验证的正是生产降级路径本身
    from debate.services.cache import RedisCache

    env.app.state.svc.cache = RedisCache(BrokenClient())
    resp = await client.get("/api/topics")
    assert resp.status_code == 200 and "items" in resp.json()
    created = await client.post("/api/topics", json={
        "title": "降级期间新建 / 甲乙", "pro_points": "p", "con_points": "c"})
    assert created.status_code == 201  # 失效失败也不影响写成功
    titles = [t["title"] for t in (await client.get("/api/topics")).json()["items"]]
    assert "降级期间新建 / 甲乙" in titles
    await client.aclose()
