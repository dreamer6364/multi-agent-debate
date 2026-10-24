"""比赛端点集成测试（TEST_PLAN IT-DEB/IT-SPC/SSE 前置/AC-10~12，SPEC §5.2）。"""
import asyncio

import pytest

from .conftest import (
    BlockingBackend,
    fake_gateway_factory,
    make_app,
    new_client,
    register_and_login,
    wait_status,
)
from debate.engine.llm.gateway import Gateway
from debate.engine.schema import Camp
from httpx import ASGITransport, AsyncClient


async def _start(client, **overrides):
    body = {"topic_id": 1, "format": "lite"}
    body.update(overrides)
    response = await client.post("/api/debates", json=body)
    assert response.status_code == 202, response.text
    return response.json()["id"]


async def test_create_run_and_archive(env):
    client = await new_client(env)
    await register_and_login(client, "alice")
    debate_id = await _start(client)

    detail = await wait_status(client, debate_id, "finished")
    assert detail["winner"] == "pro"
    assert detail["votes"] == {"pro": 2, "con": 1}
    assert detail["usage"]["calls"] > 0
    assert detail["started_at"] and detail["finished_at"]

    speeches = (await client.get(f"/api/debates/{debate_id}/speeches")).json()
    assert speeches["has_more"] is False
    seqs = [s["seq"] for s in speeches["items"]]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))
    assert speeches["items"][0]["phase"] == 2  # lite 从立论开始

    incremental = (await client.get(f"/api/debates/{debate_id}/speeches", params={"after_seq": seqs[3]})).json()
    assert [s["seq"] for s in incremental["items"]] == seqs[4:]  # after_seq 增量语义

    listed = (await client.get("/api/debates")).json()
    assert listed["total"] == 1 and listed["items"][0]["status"] == "finished"
    await client.aclose()


async def test_ownership_isolation(env):
    alice, bob = await new_client(env), await new_client(env)
    await register_and_login(alice, "alice")
    await register_and_login(bob, "bob")
    debate_id = await _start(alice)
    await wait_status(alice, debate_id, "finished")

    for path in (f"/api/debates/{debate_id}", f"/api/debates/{debate_id}/speeches"):
        response = await bob.get(path)
        assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"  # AC-11
    stop = await bob.post(f"/api/debates/{debate_id}/stop")
    assert stop.status_code == 404
    await alice.aclose()
    await bob.aclose()


async def test_validation_and_topic_404(env):
    client = await new_client(env)
    await register_and_login(client, "alice")
    both = await client.post("/api/debates", json={"topic_id": 1, "custom_topic": {
        "title": "t", "pro_points": "a", "con_points": "b"}, "format": "lite"})
    assert both.status_code == 422
    bad_format = await client.post("/api/debates", json={"topic_id": 1, "format": "quick"})
    assert bad_format.status_code == 422
    bad_judges = await client.post("/api/debates", json={"topic_id": 1, "format": "lite", "judges": 2})
    assert bad_judges.status_code == 422
    missing_topic = await client.post("/api/debates", json={"topic_id": 99999, "format": "lite"})
    assert missing_topic.status_code == 404
    await client.aclose()


async def test_custom_topic_flow(env):
    client = await new_client(env)
    await register_and_login(client, "alice")
    created = await client.post("/api/debates", json={
        "custom_topic": {"title": "熬夜是当代青年的自由 / 也需要节制",
                          "pro_points": "自主权", "con_points": "健康成本"},
        "format": "lite"})
    assert created.status_code == 202
    detail = await wait_status(client, created.json()["id"], "finished")
    assert detail["topic"]["title"] == "熬夜是当代青年的自由 / 也需要节制"
    await client.aclose()


async def test_concurrent_quota(env, backend_root, tmp_path):
    backend = BlockingBackend()
    app = make_app(backend_root, tmp_path,
                   gateway_factory=lambda config, fmt: Gateway(
                       backend=backend, model_debater="fake", model_official="fake",
                       max_retries=0, sleep=lambda s: None))
    async with app.router.lifespan_context(app):
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        await register_and_login(client, "alice")
        first = await client.post("/api/debates", json={"topic_id": 1, "format": "lite"})
        assert first.status_code == 202
        await asyncio.to_thread(backend.started.wait, 10)  # 线程内等待，避免饿死事件循环
        second = await client.post("/api/debates", json={"topic_id": 1, "format": "lite"})
        assert second.status_code == 429 and second.json()["code"] == "QUOTA_CONCURRENT"  # AC-12
        backend.release.set()
        await wait_status(client, first.json()["id"], "finished")
        await client.aclose()


async def test_daily_quota_and_rollover(env, backend_root, tmp_path, tomorrow_fn):
    app = make_app(backend_root, tmp_path)
    async with app.router.lifespan_context(app):
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        await register_and_login(client, "alice")
        for _ in range(5):
            debate_id = (await client.post("/api/debates", json={
                "topic_id": 1, "format": "lite"})).json()["id"]
            await wait_status(client, debate_id, "finished")
        sixth = await client.post("/api/debates", json={"topic_id": 1, "format": "lite"})
        assert sixth.status_code == 429 and sixth.json()["code"] == "QUOTA_DAILY"  # AC-12

        app.state.svc.service.now_fn = tomorrow_fn()  # 东八区自然日切换后配额恢复（配额读服务内部时钟）
        seventh = await client.post("/api/debates", json={"topic_id": 1, "format": "lite"})
        assert seventh.status_code == 202
        await wait_status(client, seventh.json()["id"], "finished")
        await client.aclose()


async def test_stop_running_debate(env, backend_root, tmp_path):
    backend = BlockingBackend()
    app = make_app(backend_root, tmp_path,
                   gateway_factory=lambda config, fmt: Gateway(
                       backend=backend, model_debater="fake", model_official="fake",
                       max_retries=0, sleep=lambda s: None))
    async with app.router.lifespan_context(app):
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        await register_and_login(client, "alice")
        debate_id = (await client.post("/api/debates", json={"topic_id": 1, "format": "lite"})).json()["id"]
        await asyncio.to_thread(backend.started.wait, 10)  # 线程内等待，避免饿死事件循环

        stopped = await client.post(f"/api/debates/{debate_id}/stop")
        assert stopped.status_code == 200 and stopped.json()["status"] == "stopped"
        backend.release.set()
        detail = await wait_status(client, debate_id, "stopped")
        assert detail["status"] == "stopped"  # 协作式取消，节点边界退出

        again = await client.post(f"/api/debates/{debate_id}/stop")
        assert again.status_code == 409 and again.json()["code"] == "DEBATE_NOT_RUNNING"
        await client.aclose()


async def test_export_md_and_html(env):
    client = await new_client(env)
    await register_and_login(client, "alice")
    debate_id = await _start(client)
    await wait_status(client, debate_id, "finished")

    md = await client.get(f"/api/debates/{debate_id}/export")
    assert md.status_code == 200
    assert "text/markdown" in md.headers["content-type"]
    assert md.text.startswith("# 辩论实录：")
    assert "**正方·一辩**：" in md.text

    html = await client.get(f"/api/debates/{debate_id}/export", params={"format": "html"})
    assert html.status_code == 200
    assert "text/html" in html.headers["content-type"]
    assert "<script" not in html.text.lower()  # 零 JS（CONSTRAINTS F6）
    assert 'src="http' not in html.text and 'href="http' not in html.text  # 零外部请求
    assert "本场胜方：正方" in html.text  # 胜方横幅
    assert 'class="speech pro"' in html.text  # 角色配色 token
    assert "<svg" in html.text and "polygon" in html.text  # 内联 SVG 雷达图
    assert "逻辑评委" in html.text  # 导航与评分表

    bad = await client.get(f"/api/debates/{debate_id}/export", params={"format": "pdf"})
    assert bad.status_code == 422
    await client.aclose()


async def test_export_not_ready(env, backend_root, tmp_path):
    backend = BlockingBackend()
    app = make_app(backend_root, tmp_path,
                   gateway_factory=lambda config, fmt: Gateway(
                       backend=backend, model_debater="fake", model_official="fake",
                       max_retries=0, sleep=lambda s: None))
    async with app.router.lifespan_context(app):
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        await register_and_login(client, "alice")
        debate_id = (await client.post("/api/debates", json={"topic_id": 1, "format": "lite"})).json()["id"]
        await asyncio.to_thread(backend.started.wait, 10)  # 线程内等待，避免饿死事件循环
        response = await client.get(f"/api/debates/{debate_id}/export")
        assert response.status_code == 409 and response.json()["code"] == "EXPORT_NOT_READY"
        backend.release.set()
        await wait_status(client, debate_id, "finished")
        await client.aclose()


async def test_health(env):
    client = await new_client(env)
    response = await client.get("/api/health")
    assert response.status_code == 200 and response.json() == {"status": "ok", "db": "ok"}
    await client.aclose()
