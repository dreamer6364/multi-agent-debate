"""诊断 sse05：fault(http_500) 场次的 speech 文本与 status/error。"""
import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from httpx import AsyncClient  # noqa: E402

from debate.engine.llm.gateway import Gateway  # noqa: E402
from tests.api.conftest import LiveServer, make_app, register_and_login, wait_status  # noqa: E402
from tests.api.test_sse import collect, parse_sse, stream_lines  # noqa: E402
from tests.fakes.llm import FakeLLM  # noqa: E402


async def main() -> None:
    app = make_app(BACKEND_ROOT, None,
                   gateway_factory=lambda config, fmt: Gateway(
                       backend=FakeLLM(mode="fault", error_kind="http_500"),
                       model_debater="fake", model_official="fake",
                       max_retries=0, sleep=lambda s: None))
    live = LiveServer(app).start()
    client = AsyncClient(base_url=live.base_url)
    try:
        try:
            await register_and_login(client, "alice")
        except AssertionError:
            await client.post("/api/auth/login", json={"username": "alice", "password": "password8"})
        debate_id = (await client.post("/api/debates", json={"topic_id": 1, "format": "lite"})).json()["id"]
        detail = await wait_status(client, debate_id, "failed", timeout=30)
        print("status:", detail["status"], "| error:", detail.get("error"))
        sink: list[str] = []
        await asyncio.wait_for(stream_lines(client, debate_id, sink), timeout=30)
        events = parse_sse(sink)
        speeches = [e for e in events if e["event"] == "speech"]
        print("speech count:", len(speeches))
        for s in speeches:
            d = s["data"]
            print(f"  seq={d['seq']} speaker={d['speaker']} text={d['text'][:40]!r}")
        tail = [e["event"] for e in events[-3:]]
        print("tail events:", tail)
        last = events[-1]
        print("last event:", last["event"], last["data"].get("status"), repr(last["data"].get("error"))[:80])
    finally:
        await client.aclose()
        live.stop()


asyncio.run(main())
