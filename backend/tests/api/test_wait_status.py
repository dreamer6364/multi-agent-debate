"""ISS-014 B-4：wait_status 事件驱动根治满载 flake。

回归锁定：
- 事件驱动快路径：终态 status 事件一到即返回（早于 DB commit，不受写库排队/墙钟影响）；
- 终态先于订阅发出时：即时回读兜底（不白等 timeout）；
- 无 app/bus 的裸 client：回落轮询（兼容既有调用）。
"""
from __future__ import annotations

import asyncio

from tests.api.conftest import wait_status


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """最小 client 替身：GET 返回预置状态序列；可挂 _test_app 提供 EventBus。"""

    def __init__(self, statuses: list[str], *, app=None):
        self._statuses = list(statuses)
        self._test_app = app
        self.gets = 0

    async def get(self, url):
        self.gets += 1
        status = self._statuses[min(self.gets - 1, len(self._statuses) - 1)]
        return _FakeResponse({"status": status})


class _FakeService:
    def __init__(self, bus):
        self._buses = {7: bus}


class _FakeState:
    def __init__(self, bus):
        self.service = _FakeService(bus)  # 对齐 AppState.service（DebateService）


class _FakeApp:
    def __init__(self, bus):
        self.state = type("S", (), {"svc": _FakeState(bus)})()  # app.state.svc → AppState-like


class _FakeBus:
    def __init__(self):
        self._subscribers = set()

    def subscribe(self):
        queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
        self._subscribers.discard(queue)

    def publish(self, event):
        for q in list(self._subscribers):
            q.put_nowait(event)


async def test_event_fast_path_returns_before_polling():
    """终态事件到达即返回——即使 GET 永远只回 running（模拟 DB commit 排队）。"""
    from unittest.mock import patch

    bus = _FakeBus()
    client = _FakeClient(["running"], app=_FakeApp(bus))  # GET 恒 running
    # 订阅后异步发布终态
    async def publish_later():
        await asyncio.sleep(0.02)
        bus.publish({"event": "status", "data": {"status": "finished"}})

    task = asyncio.create_task(publish_later())
    with patch("tests.api.conftest._fetch_debate", new=lambda c, d: asyncio.sleep(0, result={"status": "finished"})):
        result = await wait_status(client, 7, "finished", timeout=2.0)
    await task
    assert result == {"status": "finished"}
    assert client.gets == 1  # 仅订阅前的即时回读 1 次；事件快路径无需轮询


async def test_immediate_read_when_terminal_already_committed():
    """终态已落库、事件早于订阅发出：即时回读直接返回，不白等 timeout。"""
    bus = _FakeBus()
    client = _FakeClient(["finished"], app=_FakeApp(bus))
    result = await wait_status(client, 7, "finished", timeout=2.0)
    assert result == {"status": "finished"}
    assert client.gets == 1


async def test_bare_client_falls_back_to_polling():
    """无 app 的裸 client：轮询兜底仍工作（兼容既有调用点）。"""
    client = _FakeClient(["running", "running", "finished"])
    result = await wait_status(client, 7, "finished", timeout=2.0)
    assert result == {"status": "finished"}
    assert client.gets >= 3
