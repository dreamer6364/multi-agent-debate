"""API 集成测试基建（TEST_PLAN §2.2/§5）。

测试库：SQLite(aiosqlite, tmp 文件)，生产路径为 MySQL（SQLModel 可移植）；
LLM 一律 FakeLLM 剧本回放，不出网。
SSE 直播测试用真实 uvicorn 线程服务器（ASGITransport 会缓冲完整响应体，无法验证流式语义）。
"""
from __future__ import annotations

import socket
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlmodel import SQLModel

from debate.api.app import create_app
from debate.engine.llm.gateway import Gateway

_TZ = ZoneInfo("Asia/Shanghai")

# v2.2：测试库全面切 MySQL（mad_test），用例间 TRUNCATE；SQLite 仅作为可选回落
TEST_DB_URL = "mysql+asyncmy://root:12345678mxy@localhost:3306/mad_test?charset=utf8mb4"


class LiveServer:
    """在后台线程跑真实 uvicorn（单 worker），供流式语义测试使用。"""

    def __init__(self, app):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        self.base_url = f"http://127.0.0.1:{port}"
        self._thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> "LiveServer":
        self._thread.start()
        deadline = time.time() + 15
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        if not self.server.started:
            raise TimeoutError("uvicorn 未能在时限内启动")
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=15)


def fake_gateway_factory(backend_root: Path, script_fmt: str | None = None):
    from tests.fakes.llm import FakeLLM

    def build(config, fmt):
        name = script_fmt or fmt.value
        script = backend_root / "tests" / "fakes" / f"script_{name}.json"
        return Gateway(backend=FakeLLM.scripted(script), model_debater="fake",
                       model_official="fake", max_retries=1, sleep=lambda s: None)

    return build


class BlockingBackend:
    """阻塞式替身：首条调用挂起，用于并发/终止/导出时机的测试。

    释放后：辩手调用返回定长句子；评委调用返回合法 lite 六维 JSON（保证评分链路真实走通）。
    """

    JUDGE_JSON = (
        '{"pro_scores":{"statement":20,"rebuttal_cross":30,"free":15,"closing":10,"conduct":5},'
        '"con_scores":{"statement":18,"rebuttal_cross":28,"free":14,"closing":11,"conduct":4},'
        '"vote":"pro","best_speaker":"pro-1",'
        '"highlights":{"pro":["“判准”清晰","“对冲”具体"],"con":["“安全阀”贴切","“复核”明确"]},'
        '"weaknesses":{"pro":["“可控”缺证","“量化”未完"],"con":["“边界”含糊","“案例”欠缺"]}}'
    )
    DEBATER_TEXT = "这是一条等待释放后的发言，长度可控，用于阻塞回放场景的断言。"

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(self, messages, **kwargs):
        self.started.set()
        self.release.wait(timeout=30)
        last = messages[-1]["content"] if messages else ""
        if "评审任务" in last or "只输出一个 JSON" in last:
            return self.JUDGE_JSON
        return self.DEBATER_TEXT

    def stream(self, messages, **kwargs):
        self.started.set()
        self.release.wait(timeout=30)
        text = self.complete(messages, **kwargs)
        for i in range(0, len(text), 4):
            yield text[i : i + 4]


def make_app(backend_root: Path, tmp_path: Path | None = None, *, gateway_factory=None, now_fn=None, **kwargs):
    return create_app(
        config_path=backend_root / "config.yaml",
        db_url=TEST_DB_URL,
        jwt_secret="test-secret",
        config_overrides={"procedure": {"phases": {"free_debate": 1.0}}, "tools": {"elenchus": {"enabled": False}}},
        gateway_factory=gateway_factory or fake_gateway_factory(backend_root),
        compressor_factory=lambda config, gateway: _fake_columns,
        now_fn=now_fn,
        **kwargs,
    )


def _fake_columns(speeches):
    if not speeches:
        return {}
    return {"definition_judge": "双方定义与判准已记录在案。", "arguments": "论点继续展开。",
            "clashes": "交锋持续。", "fallacies": "互相指出瑕疵。", "situation": "局势推进。"}


@pytest.fixture(autouse=True)
async def _mysql_clean_db():
    """每个用例前：建表 + 清空全部业务表（MySQL mad_test）。"""
    from sqlalchemy.ext.asyncio import create_async_engine as _cae
    from sqlmodel import SQLModel as _SM

    engine = _cae(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(_SM.metadata.create_all)
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for t in reversed(_SM.metadata.sorted_tables):
            await conn.execute(text(f"TRUNCATE TABLE {t.name}"))
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    yield
    await engine.dispose()


@pytest.fixture
async def env(backend_root, tmp_path):
    app = make_app(backend_root, tmp_path)
    async with app.router.lifespan_context(app):
        async def make_client() -> AsyncClient:
            return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

        yield SimpleNamespace(app=app, make_client=make_client)


async def new_client(env) -> AsyncClient:
    return await env.make_client()


async def new_live_client(live) -> AsyncClient:
    """连接真实 uvicorn 线程服务器的客户端（流式语义测试用）。"""
    return AsyncClient(base_url=live.base_url)


async def register_and_login(client: AsyncClient, username: str = "alice", password: str = "password8"):
    response = await client.post("/api/auth/register", json={"username": username, "password": password})
    assert response.status_code == 201, response.text
    response = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


async def wait_status(client: AsyncClient, debate_id: int, status: str, timeout: float = 15.0) -> dict:
    import asyncio

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    last = {}
    while loop.time() < deadline:
        response = await client.get(f"/api/debates/{debate_id}")
        last = response.json()
        if last.get("status") == status:
            return last
        await asyncio.sleep(0.05)
    raise TimeoutError(f"等待 {status} 超时，最后状态: {last}")


def _now_cn():
    return datetime.now(_TZ).replace(tzinfo=None)

