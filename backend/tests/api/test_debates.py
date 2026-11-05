"""比赛端点集成测试（TEST_PLAN IT-DEB/IT-SPC/SSE 前置/AC-10~12，SPEC §5.2）。"""
import asyncio


from .conftest import (
    BlockingBackend,
    make_app,
    new_client,
    register_and_login,
    wait_status,
)
from debate.engine.llm.gateway import Gateway
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
    assert speeches["items"][0]["phase"] == 1  # lite 也以主席开场（v2.2 补齐仪式环节）
    assert speeches["items"][0]["speaker"] == "chair"
    assert speeches["items"][-1]["phase"] == 11  # 全场以主席结束致辞收束

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


async def test_concurrent_create_race_only_one_accepted(env):
    """ISS-001 回归：同用户**同时**发起两个创建请求，必须恰好一个 202、其余 429。

    修复前 check_quota 只读不锁（TOCTOU），两个请求双双通过检查返回 202。
    """
    client = await new_client(env)
    await register_and_login(client, "alice")
    results = await asyncio.gather(
        client.post("/api/debates", json={"topic_id": 1, "format": "lite"}),
        client.post("/api/debates", json={"topic_id": 1, "format": "lite"}),
    )
    codes = [r.status_code for r in results]
    assert codes.count(202) == 1, f"配额竞态未堵住: {codes}"
    assert codes.count(429) == 1, f"期望一成一拒: {codes}"
    rejected = next(r for r in results if r.status_code == 429)
    assert rejected.json()["code"] == "QUOTA_CONCURRENT"
    accepted = next(r for r in results if r.status_code == 202)  # 收尾：停掉，避免跨用例残留任务
    await client.post(f"/api/debates/{accepted.json()['id']}/stop")
    await wait_status(client, accepted.json()["id"], "stopped")
    await client.aclose()


async def test_quota_reject_leaves_no_orphan_topic(env):
    """ISS-002 回归：配额 429 时自建辩题不得落库（修复前先插 topic 再查配额，重试即堆积重复）。"""
    client = await new_client(env)
    await register_and_login(client, "alice")
    ct = {"title": "第一场辩题 / 甲乙", "pro_points": "p1", "con_points": "c1",
          "pro_stance": "正", "con_stance": "反"}
    first = await client.post("/api/debates", json={"custom_topic": ct, "format": "lite"})
    assert first.status_code == 202, first.text

    ct2 = dict(ct, title="配额满时的辩题 / 应被回滚")
    for _ in range(3):  # 模拟用户按提示重试
        rejected = await client.post("/api/debates", json={"custom_topic": ct2, "format": "lite"})
        assert rejected.status_code == 429, rejected.text

    topics = (await client.get("/api/topics")).json()
    titles = [t["title"] for t in (topics if isinstance(topics, list) else topics.get("items", []))]
    assert "配额满时的辩题 / 应被回滚" not in titles, f"429 留下了孤儿辩题: {titles}"
    await wait_status(client, first.json()["id"], "finished")  # 收尾：等首场跑完，避免跨用例残留任务
    await client.aclose()


async def test_no_daily_quota(env):
    """v1.3：每日配额已删除——连打多场不再被 429 拦截（仅并发限 1，串行不受限）。"""
    client = await new_client(env)
    await register_and_login(client, "alice")
    for _ in range(6):  # 超过旧限额 5 场
        debate_id = (await client.post("/api/debates", json={"topic_id": 1, "format": "lite"})).json()["id"]
        detail = await wait_status(client, debate_id, "finished")
        assert detail["status"] == "finished"
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

    xlsx = await client.get(f"/api/debates/{debate_id}/export", params={"format": "xlsx"})
    assert xlsx.status_code == 200
    assert "spreadsheetml" in xlsx.headers["content-type"]
    from io import BytesIO

    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(xlsx.content))
    assert wb.sheetnames == ["评分与结果", "六维明细"]
    ws = wb["评分与结果"]
    assert ws["A1"].value.startswith("辩论评分表：")
    assert any(cell.value == "逻辑评委" for row in ws.iter_rows() for cell in row)

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


async def test_delete_archive_and_clear(env):
    """单场删除 + 清空归档：发言与评分卡一并清理，列表与详情同步消失。"""
    client = await new_client(env)
    await register_and_login(client, "alice")
    first = await _start(client)
    await wait_status(client, first, "finished")
    second = await _start(client)  # 并发限 1 场：必须等前一场收尾
    await wait_status(client, second, "finished")

    single = await client.delete(f"/api/debates/{first}")
    assert single.status_code == 204
    assert (await client.get(f"/api/debates/{first}")).status_code == 404
    assert (await client.get(f"/api/debates/{first}/speeches")).status_code == 404

    cleared = await client.delete("/api/debates")
    assert cleared.status_code == 200 and cleared.json()["deleted"] == 1
    assert (await client.get("/api/debates")).json()["total"] == 0
    assert (await client.get(f"/api/debates/{second}")).status_code == 404
    await client.aclose()


async def test_delete_running_debate_rejected(env, backend_root, tmp_path):
    """进行中的场次必须先终止：引擎仍在写库，直接删除会被 finalize 写回。"""
    backend = BlockingBackend()
    app = make_app(backend_root, tmp_path,
                   gateway_factory=lambda config, fmt: Gateway(
                       backend=backend, model_debater="fake", model_official="fake",
                       max_retries=0, sleep=lambda s: None))
    async with app.router.lifespan_context(app):
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        await register_and_login(client, "alice")
        debate_id = (await client.post("/api/debates", json={"topic_id": 1, "format": "lite"})).json()["id"]
        await asyncio.to_thread(backend.started.wait, 10)
        rejected = await client.delete(f"/api/debates/{debate_id}")
        assert rejected.status_code == 409 and rejected.json()["code"] == "DEBATE_ACTIVE"
        # 清空归档也不应误伤进行中的场次
        assert (await client.delete("/api/debates")).json()["deleted"] == 0
        assert (await client.get(f"/api/debates/{debate_id}")).status_code == 200
        backend.release.set()
        await wait_status(client, debate_id, "finished")
        await client.aclose()


async def test_start_failure_marks_failed_and_frees_quota(env):
    """v2.9.4 回归（BE-P2-1）：start 失败不留孤儿 pending。

    pending 计入并发配额（上限 1）——此前 start 抛异常会留下没有 task 的幽灵
    pending 场次，把该用户后续所有创建请求 429 堵死。修复后回写 failed 并放行。
    """
    client = await new_client(env)
    await register_and_login(client, "alice")
    service = env.app.state.svc.service
    original_start = service.start

    def boom(**kwargs):
        raise RuntimeError("gateway init failed")

    service.start = boom
    try:
        response = await client.post("/api/debates", json={"topic_id": 1, "format": "lite"})
        assert response.status_code == 500
        listed = (await client.get("/api/debates")).json()
        assert listed["total"] == 1
        assert listed["items"][0]["status"] == "failed"  # 不再是 pending 幽灵
    finally:
        service.start = original_start

    # 配额已释放：下一场可正常启动并跑完
    debate_id = await _start(client)
    await wait_status(client, debate_id, "finished")
    await client.aclose()
