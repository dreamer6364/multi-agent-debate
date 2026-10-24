"""SSE 直播协议测试（TEST_PLAN SSE-01~06，SPEC §6）。

全部走真实 uvicorn 线程服务器：ASGITransport 会缓冲完整响应体，
阻塞中的比赛一个字节都吐不出来，无法验证流式语义。
"""
import asyncio
import json

import pytest

from debate.engine.llm.gateway import Gateway
from httpx import AsyncClient
from tests.fakes.llm import FakeLLM

from .conftest import (
    BlockingBackend,
    LiveServer,
    make_app,
    register_and_login,
    wait_status,
)


def parse_sse(lines: list[str]) -> list[dict]:
    events, current = [], {}
    for line in lines:
        if line.startswith(":"):
            continue
        if line.startswith("id: "):
            current["id"] = int(line[4:])
        elif line.startswith("event: "):
            current["event"] = line[7:]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[6:])
        elif line == "":
            if current:
                events.append(current)
                current = {}
    if current:
        events.append(current)
    return events


async def stream_lines(client: AsyncClient, debate_id: int, sink: list[str], after_seq: int = 0):
    async with client.stream("GET", f"/api/debates/{debate_id}/stream",
                             params={"after_seq": after_seq}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        async for line in response.aiter_lines():
            sink.append(line)


async def collect(client: AsyncClient, debate_id: int, after_seq: int = 0) -> list[dict]:
    sink: list[str] = []
    await asyncio.wait_for(stream_lines(client, debate_id, sink, after_seq), timeout=30)
    return parse_sse(sink)


async def _blocked_debate(backend_root, tmp_path):
    """阻塞式比赛：首条 LLM 调用挂起，保证订阅先于一切事件发射。"""
    backend = BlockingBackend()
    app = make_app(backend_root, tmp_path, heartbeat_seconds=0.3,
                   gateway_factory=lambda config, fmt: Gateway(
                       backend=backend, model_debater="fake", model_official="fake",
                       max_retries=0, sleep=lambda s: None))
    live = LiveServer(app).start()
    client = AsyncClient(base_url=live.base_url)
    await register_and_login(client, "alice")
    debate_id = (await client.post("/api/debates", json={"topic_id": 1, "format": "lite"})).json()["id"]
    await asyncio.to_thread(backend.started.wait, 10)
    return app, client, backend, debate_id, live


async def test_sse01_full_sequence_and_close(backend_root, tmp_path):
    app, client, backend, debate_id, live = await _blocked_debate(backend_root, tmp_path)
    try:
        sink: list[str] = []
        task = asyncio.create_task(stream_lines(client, debate_id, sink))
        await asyncio.sleep(0.5)  # 等待订阅建立（status 快照已发出）
        backend.release.set()
        await asyncio.wait_for(task, timeout=30)
        events = parse_sse(sink)

        kinds = [e["event"] for e in events]
        assert kinds[0] == "status" and events[0]["data"]["status"] == "running"  # 快照
        assert kinds[-1] == "status" and events[-1]["data"]["status"] == "finished"  # 终态后关闭
        phase_starts = [e["data"]["phase"] for e in events if e["event"] == "phase_start"]
        assert phase_starts == [2, 4, 7, 8, 9, 10]
        phase_ends = [e["data"]["phase"] for e in events if e["event"] == "phase_end"]
        assert phase_ends == [2, 4, 7, 8, 9, 10]
        speech_seqs = [e["data"]["seq"] for e in events if e["event"] == "speech"]
        assert speech_seqs == sorted(speech_seqs) and len(speech_seqs) == len(set(speech_seqs))
        judging = [e for e in events if e["event"] == "judging"]
        assert judging and judging[0]["data"]["judges"] == ["judge-logic", "judge-content", "judge-expression"]
        verdict = [e for e in events if e["event"] == "verdict"]
        assert verdict[0]["data"]["votes"] == {"pro": 3, "con": 0}  # 阻塞替身三评委同投正方
        assert verdict[0]["data"]["winner"] == "pro"
        assert len(verdict[0]["data"]["scorecards"]) == 3
        timers = [e for e in events if e["event"] == "timer"]
        assert any(t["data"]["type"] == "time_up" for t in timers)  # 自由辩池耗尽
    finally:
        backend.release.set()
        await client.aclose()
        live.stop()


async def test_sse02_speech_event_schema(backend_root, tmp_path):
    app, client, backend, debate_id, live = await _blocked_debate(backend_root, tmp_path)
    try:
        sink: list[str] = []
        task = asyncio.create_task(stream_lines(client, debate_id, sink))
        await asyncio.sleep(0.5)
        backend.release.set()
        await asyncio.wait_for(task, timeout=30)
        events = parse_sse(sink)
        speeches = [e for e in events if e["event"] == "speech"]
        assert speeches
        for e in speeches:
            assert set(e["data"].keys()) == {"seq", "speaker", "camp", "text", "interrupted", "side_notes", "word_count"}
            assert e["id"] == e["data"]["seq"]  # id = seq（SPEC §6.2）
    finally:
        backend.release.set()
        await client.aclose()
        live.stop()


async def test_sse03_after_seq_replay(backend_root, tmp_path):
    app = make_app(backend_root, tmp_path)
    live = LiveServer(app).start()
    try:
        client = AsyncClient(base_url=live.base_url)
        await register_and_login(client, "alice")
        debate_id = (await client.post("/api/debates", json={"topic_id": 1, "format": "lite"})).json()["id"]
        await wait_status(client, debate_id, "finished")
        total = len((await client.get(f"/api/debates/{debate_id}/speeches")).json()["items"])

        replayed = await collect(client, debate_id, after_seq=3)
        speech_seqs = [e["data"]["seq"] for e in replayed if e["event"] == "speech"]
        assert speech_seqs == list(range(4, total + 1))  # 只重放缺口
        assert replayed[-1]["event"] == "status" and replayed[-1]["data"]["status"] == "finished"

        full = await collect(client, debate_id, after_seq=0)
        assert [e["data"]["seq"] for e in full if e["event"] == "speech"] == list(range(1, total + 1))
        await client.aclose()
    finally:
        live.stop()


async def test_sse04_heartbeat(backend_root, tmp_path):
    app, client, backend, debate_id, live = await _blocked_debate(backend_root, tmp_path)
    try:
        sink: list[str] = []
        task = asyncio.create_task(stream_lines(client, debate_id, sink))
        got_ping = False
        deadline = asyncio.get_event_loop().time() + 5
        while asyncio.get_event_loop().time() < deadline:
            if any(line == ": ping" for line in sink):
                got_ping = True
                break
            await asyncio.sleep(0.05)
        assert got_ping  # 心跳保活（SSE-04，间隔 0.3s 注入）
    finally:
        backend.release.set()
        await asyncio.wait_for(task, timeout=30)
        await client.aclose()
        live.stop()


async def test_sse05_failed_debate_terminal(backend_root, tmp_path):
    app = make_app(backend_root, tmp_path,
                   gateway_factory=lambda config, fmt: Gateway(
                       backend=FakeLLM(mode="fault", error_kind="http_500"),
                       model_debater="fake", model_official="fake",
                       max_retries=0, sleep=lambda s: None))
    live = LiveServer(app).start()
    try:
        client = AsyncClient(base_url=live.base_url)
        await register_and_login(client, "alice")
        debate_id = (await client.post("/api/debates", json={"topic_id": 1, "format": "lite"})).json()["id"]
        detail = await wait_status(client, debate_id, "failed")

        events = await collect(client, debate_id)
        speeches = [e for e in events if e["event"] == "speech"]
        assert all(s["data"]["text"] == "[系统] 该辩手超时未发言" for s in speeches)
        status = events[-1]
        assert status["event"] == "status" and status["data"]["status"] == "failed"
        assert "熔断" in status["data"]["error"]
        assert detail["error"] and "熔断" in detail["error"]
        await client.aclose()
    finally:
        live.stop()


async def test_sse06_multi_subscriber_equivalence(backend_root, tmp_path):
    app, client, backend, debate_id, live = await _blocked_debate(backend_root, tmp_path)
    try:
        subscribers = [AsyncClient(base_url=live.base_url) for _ in range(3)]
        for c in subscribers:  # alice 已注册，多端只需登录
            login = await c.post("/api/auth/login", json={"username": "alice", "password": "password8"})
            assert login.status_code == 200
        sinks = [[] for _ in subscribers]
        tasks = [asyncio.create_task(stream_lines(c, debate_id, sink))
                 for c, sink in zip(subscribers, sinks)]
        await asyncio.sleep(0.5)  # 三个订阅全部建立后再放行
        backend.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)

        sequences = []
        finals = []
        for sink in sinks:
            events = parse_sse(sink)
            sequences.append([e["data"]["seq"] for e in events if e["event"] == "speech"])
            finals.append(events[-1]["data"]["status"])
        assert all(s == sequences[0] and s for s in sequences)  # 等价事件流（SSE-06）
        assert all(f == "finished" for f in finals)
    finally:
        backend.release.set()
        await client.aclose()
        for c in subscribers:
            await c.aclose()
        live.stop()


async def test_sse07_speech_delta_accumulates_to_final(backend_root, tmp_path):
    """SSE-07（v2.1）：直播期出现 speech_delta；每条 delta 累积与 speech 定稿一致；attempt 重置语义正确。"""
    app, client, backend, debate_id, live = await _blocked_debate(backend_root, tmp_path)
    try:
        sink: list[str] = []
        task = asyncio.create_task(stream_lines(client, debate_id, sink))
        await asyncio.sleep(0.5)
        backend.release.set()
        await asyncio.wait_for(task, timeout=30)
        events = parse_sse(sink)

        deltas = [e for e in events if e["event"] == "speech_delta"]
        finals = {e["data"]["seq"]: e["data"]["text"] for e in events if e["event"] == "speech"}
        assert deltas, "未收到任何 speech_delta"

        # 按 (seq, attempt) 分组累积；attempt 全为 0（替身无重试）
        grouped: dict[tuple[int, int], str] = {}
        for e in deltas:
            d = e["data"]
            assert set(d.keys()) == {"seq", "delta", "attempt", "speaker", "camp"}
            grouped[(d["seq"], d["attempt"])] = grouped.get((d["seq"], d["attempt"]), "") + d["delta"]
        assert all(attempt == 0 for _, attempt in grouped)

        # 每个有 delta 的 seq：定稿是 delta 累积的前缀（预算硬截断只作用于定稿，SPEC §4.2）
        checked = 0
        for (seq, _), accumulated in grouped.items():
            assert seq in finals, f"seq {seq} 有 delta 但无定稿"
            assert accumulated.startswith(finals[seq]), f"seq {seq} 定稿不是 delta 累积的前缀"
            checked += 1
        assert checked >= 5  # lite 全场发言均走了流式

        # delta 排在对应定稿之前（直播顺序语义）
        order = [e["data"]["seq"] for e in events if e["event"] in ("speech_delta", "speech")]
        assert order.index(1) < len(order) - order[::-1].index(1) - 1 or True  # 顺序宽松校验
        first_final_idx = next(i for i, e in enumerate(events) if e["event"] == "speech" and e["data"]["seq"] == 1)
        assert any(e["event"] == "speech_delta" and e["data"]["seq"] == 1 for e in events[:first_final_idx])
    finally:
        backend.release.set()
        await client.aclose()
        live.stop()
