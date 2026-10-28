"""辩题端点集成测试（TEST_PLAN IT-TOP-01~04，SPEC §5.2）。"""
import pytest

from .conftest import new_client, register_and_login, wait_status


async def test_list_contains_builtin_and_own(env):
    alice, bob = await new_client(env), await new_client(env)
    await register_and_login(alice, "alice")
    await register_and_login(bob, "bob")

    created = await alice.post("/api/topics", json={
        "title": "自定义辩题：甲 / 乙", "pro_points": "甲要点", "con_points": "乙要点"})
    assert created.status_code == 201

    items = (await alice.get("/api/topics")).json()["items"]
    builtin = [t for t in items if t["is_builtin"]]
    own = [t for t in items if not t["is_builtin"]]
    assert len(builtin) == 3  # 种子辩题
    assert len(own) == 1 and own[0]["title"] == "自定义辩题：甲 / 乙"
    assert own[0]["pro_stance"] == "自定义辩题：甲"  # 缺省立场 = 标题前半句（v2.3 起不再加「支持：」前缀）

    bob_items = (await bob.get("/api/topics")).json()["items"]
    assert all(t["id"] != own[0]["id"] for t in bob_items)  # 看不到他人辩题
    await alice.aclose()
    await bob.aclose()


async def test_delete_builtin_forbidden(env):
    client = await new_client(env)
    await register_and_login(client, "alice")
    builtin_id = (await client.get("/api/topics")).json()["items"][0]["id"]
    response = await client.delete(f"/api/topics/{builtin_id}")
    assert response.status_code == 403 and response.json()["code"] == "FORBIDDEN"
    await client.aclose()


async def test_delete_other_topic_leaks_nothing(env):
    alice, bob = await new_client(env), await new_client(env)
    await register_and_login(alice, "alice")
    await register_and_login(bob, "bob")
    created = (await alice.post("/api/topics", json={
        "title": "甲 / 乙", "pro_points": "甲", "con_points": "乙"})).json()
    response = await bob.delete(f"/api/topics/{created['id']}")
    assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"
    await alice.aclose()
    await bob.aclose()


async def test_delete_topic_in_use(env):
    client = await new_client(env)
    await register_and_login(client, "alice")
    created = (await client.post("/api/topics", json={
        "title": "自定义：甲 / 乙", "pro_points": "甲要点", "con_points": "乙要点"})).json()
    debate = await client.post("/api/debates", json={"topic_id": created["id"], "format": "lite"})
    assert debate.status_code == 202
    response = await client.delete(f"/api/topics/{created['id']}")
    assert response.status_code == 409 and response.json()["code"] == "TOPIC_IN_USE"
    await client.aclose()


async def test_delete_topic_force_cascades_history(env):
    """force=true：辩题连同名下历史场次一起删除（发言/评分卡不留孤儿）。"""
    client = await new_client(env)
    await register_and_login(client, "alice")
    created = (await client.post("/api/topics", json={
        "title": "强制删除：甲 / 乙", "pro_points": "甲要点", "con_points": "乙要点"})).json()
    debate_id = (await client.post("/api/debates", json={"topic_id": created["id"], "format": "lite"})).json()["id"]
    await wait_status(client, debate_id, "finished")

    forced = await client.delete(f"/api/topics/{created['id']}", params={"force": True})
    assert forced.status_code == 204
    remaining = [t["id"] for t in (await client.get("/api/topics")).json()["items"]]
    assert created["id"] not in remaining
    assert (await client.get("/api/debates")).json()["total"] == 0
    assert (await client.get(f"/api/debates/{debate_id}")).status_code == 404
    await client.aclose()
