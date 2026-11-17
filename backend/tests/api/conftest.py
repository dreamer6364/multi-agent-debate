"""API 集成测试基建（TEST_PLAN §2.2/§5）。

测试库（ISS-005）：优先 TEST_MYSQL_URL 环境变量；否则复用 backend/.env 的 MYSQL_URL
（口令不再硬编码入库），库名替换为按 pytest-xdist worker 隔离的 mad_test[_gwN]；
两者皆无时回落 SQLite（aiosqlite 临时文件）。用例间清表。
LLM 一律 FakeLLM 剧本回放，不出网。
SSE 直播测试用真实 uvicorn 线程服务器（ASGITransport 会缓冲完整响应体，无法验证流式语义）。
"""
from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from debate.api.app import create_app
from debate.core.config import load_env
from debate.engine.llm.gateway import Gateway
from debate.services.cache import MemoryCache

_TZ = ZoneInfo("Asia/Shanghai")

# ---- 测试库解析（ISS-005：并行隔离 + 口令外置） ----
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")  # xdist worker 名（gw0/gw1…），无则空
TEST_DB_NAME = f"mad_test_{_WORKER}" if _WORKER else "mad_test"
_db_bootstrapped = False


def _swap_database(url: str, db_name: str) -> str:
    """替换 URL 中的库名，保留 scheme/账号/端口/查询参数（口令随来源环境变量，不入库）。"""
    parts = urlsplit(url)
    query = parts.query or "charset=utf8mb4"
    if "charset" not in query:
        query += "&charset=utf8mb4"
    return urlunsplit((parts.scheme, parts.netloc, f"/{db_name}" if db_name else "", query, ""))


def _resolve_test_db_url() -> str:
    explicit = os.environ.get("TEST_MYSQL_URL")
    if explicit:
        return _swap_database(explicit, TEST_DB_NAME)
    # 复用后端 .env 的连接配置（与生产同源；.env 已被 .gitignore 排除）
    load_env(Path(__file__).resolve().parents[2] / ".env")
    base = os.environ.get("MYSQL_URL")
    if base:
        return _swap_database(base, TEST_DB_NAME)
    suffix = f"_{_WORKER}" if _WORKER else ""
    return f"sqlite+aiosqlite:///{Path(tempfile.gettempdir()) / f'mad_test{suffix}.db'}"


TEST_DB_URL = _resolve_test_db_url()
IS_MYSQL = TEST_DB_URL.startswith("mysql")


class LiveServer:
    """在后台线程跑真实 uvicorn（单 worker），供流式语义测试使用。"""

    def __init__(self, app):
        self.app = app  # ISS-014 B-4：暴露给 new_live_client 订阅 EventBus
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


def make_app(backend_root: Path, tmp_path: Path | None = None, *, gateway_factory=None, now_fn=None,
             cache=None, **kwargs):
    return create_app(
        config_path=backend_root / "config.yaml",
        db_url=TEST_DB_URL,
        jwt_secret="test-secret",
        config_overrides={"procedure": {"phases": {"free_debate": 1.0}}, "tools": {"elenchus": {"enabled": False}}},
        gateway_factory=gateway_factory or fake_gateway_factory(backend_root),
        compressor_factory=lambda config, gateway: _fake_columns,
        now_fn=now_fn,
        # 测试默认注入全新进程内缓存：即便环境设了 REDIS_URL 也不连真 Redis
        # （否则库被逐用例 TRUNCATE 而 Redis 键残留，会产生跨用例脏命中）
        cache=cache if cache is not None else MemoryCache(),
        **kwargs,
    )


def _fake_columns(speeches):
    if not speeches:
        return {}
    return {"definition_judge": "双方定义与判准已记录在案。", "arguments": "论点继续展开。",
            "clashes": "交锋持续。", "fallacies": "互相指出瑕疵。", "situation": "局势推进。"}


@pytest.fixture(autouse=True)
async def _mysql_clean_db():
    """每个用例前：建表 + 清空全部业务表。

    ISS-005：库名按 xdist worker 隔离（mad_test / mad_test_gw0…），并行不互踩；
    MySQL 连接配置来自 TEST_MYSQL_URL 或 backend/.env，仓库内不再出现明文口令。
    """
    global _db_bootstrapped
    from sqlalchemy.ext.asyncio import create_async_engine as _cae
    from sqlmodel import SQLModel as _SM

    if IS_MYSQL and not _db_bootstrapped:
        # 首次运行/新 worker：测试库可能尚不存在，先以无库连接建库
        admin = _cae(_swap_database(TEST_DB_URL, ""))
        async with admin.begin() as conn:
            await conn.execute(text(
                f"CREATE DATABASE IF NOT EXISTS `{TEST_DB_NAME}` DEFAULT CHARACTER SET utf8mb4"
            ))
        await admin.dispose()
        _db_bootstrapped = True

    engine = _cae(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(_SM.metadata.create_all)
        if IS_MYSQL:
            await conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
            for t in reversed(_SM.metadata.sorted_tables):
                await conn.execute(text(f"TRUNCATE TABLE {t.name}"))
            await conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        else:
            for t in reversed(_SM.metadata.sorted_tables):
                await conn.execute(text(f"DELETE FROM {t.name}"))
    yield
    await engine.dispose()


@pytest.fixture
async def env(backend_root, tmp_path):
    app = make_app(backend_root, tmp_path)
    async with app.router.lifespan_context(app):
        async def make_client() -> AsyncClient:
            client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
            client._test_app = app  # ISS-014 B-4：供 wait_status 订阅 EventBus 终态事件
            return client

        yield SimpleNamespace(app=app, make_client=make_client)


async def new_client(env) -> AsyncClient:
    return await env.make_client()


async def new_live_client(live) -> AsyncClient:
    """连接真实 uvicorn 线程服务器的客户端（流式语义测试用）。"""
    client = AsyncClient(base_url=live.base_url, trust_env=False)
    client._test_app = live.app  # ISS-014 B-4：供 wait_status 订阅 EventBus 终态事件
    return client


async def register_and_login(client: AsyncClient, username: str = "alice", password: str = "password8"):
    response = await client.post("/api/auth/register", json={"username": username, "password": password})
    assert response.status_code == 201, response.text
    response = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


async def wait_status(client: AsyncClient, debate_id: int, status: str, timeout: float = 30.0) -> dict:
    """等待场次到达目标状态（ISS-014 B-4 根治：事件驱动优先，轮询兜底）。

    历史缺陷：纯墙钟轮询（15s→30s）在满载下偶发越线，失败集合随机。
    修复：若 client 持有 app（new_client/new_live_client 已挂），订阅该场 EventBus，
    终态 status 事件一到立即返回——事件在 bus.close 前发出，早于 DB commit，
    既不依赖固定墙钟、也不受 DB 写库排队影响；订阅不可用时回落轮询（兼容裸 client）。
    """
    import asyncio

    # 先即时回读一次：FakeLLM 很快，终态可能已落库（事件早已发出）
    first = (await client.get(f"/api/debates/{debate_id}")).json()
    if first.get("status") == status:
        return first

    app = getattr(client, "_test_app", None)
    bus = None
    if app is not None:
        svc = app.state.svc
        bus = (getattr(svc.service, "_buses", {}) or {}).get(debate_id)
    if bus is not None:
        queue = bus.subscribe()
        try:
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                data = event.get("data", {})
                if event.get("event") == "status" and data.get("status") == status:
                    return await _fetch_debate(client, debate_id)  # 回读一次拿完整落库字段
        finally:
            bus.unsubscribe(queue)

    # 兜底轮询（无 app/bus 的裸 client；或事件窗口已错过）
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


async def _fetch_debate(client: AsyncClient, debate_id: int) -> dict:
    response = await client.get(f"/api/debates/{debate_id}")
    return response.json()


def _now_cn():
    return datetime.now(_TZ).replace(tzinfo=None)

