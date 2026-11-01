"""诊断脚本：跑一个 blocked-lite 场次，打印 phase_start/phase_end/speech/delta 序列与 speech 字段集。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from httpx import AsyncClient  # noqa: E402

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from tests.api.conftest import (  # noqa: E402
    BlockingBackend, LiveServer, make_app, register_and_login,
)
from debate.engine.llm.gateway import Gateway  # noqa: E402
from tests.api.test_sse import parse_sse, stream_lines  # noqa: E402


async def main() -> None:
    backend = BlockingBackend()
    app = make_app(BACKEND_ROOT, None, heartbeat_seconds=0.3,
                   gateway_factory=lambda config, fmt: Gateway(
                       backend=backend, model_debater="fake", model_official="fake",
                       max_retries=0, sleep=lambda s: None))
    live = LiveServer(app).start()
    client = AsyncClient(base_url=live.base_url)
    try:
        try:
            await register_and_login(client, "alice")
        except AssertionError:
            await client.post("/api/auth/login", json={"username": "alice", "password": "password8"})
        debate_id = (await client.post("/api/debates", json={"topic_id": 1, "format": "lite"})).json()["id"]
        await asyncio.to_thread(backend.started.wait, 10)
        sink: list[str] = []
        task = asyncio.create_task(stream_lines(client, debate_id, sink))
        await asyncio.sleep(0.5)
        backend.release.set()
        await asyncio.wait_for(task, timeout=30)
        events = parse_sse(sink)
        print("phase_start:", [e["data"]["phase"] for e in events if e["event"] == "phase_start"])
        print("phase_end  :", [e["data"]["phase"] for e in events if e["event"] == "phase_end"])
        kinds = [e["event"] for e in events]
        print("event kinds order:", [k for k in kinds if k not in ("speech", "speech_delta")])
        speech_keys = set()
        for e in events:
            if e["event"] == "speech":
                speech_keys |= set(e["data"].keys())
        print("speech data keys union:", sorted(speech_keys))
        # delta 与 speech 的顺序（前 12 条相关事件）
        seq = [(e["event"], e["data"].get("seq"), e["data"].get("attempt"))
               for e in events if e["event"] in ("speech", "speech_delta")]
        print("first 20 speech/delta:", seq[:20])
        print("count speech:", sum(1 for k, _, _ in seq if k == "speech"),
              "count delta:", sum(1 for k, _, _ in seq if k == "speech_delta"))
        first_final = next(i for i, e in enumerate(events) if e["event"] == "speech")
        print("events before first final:", [(e["event"], e["data"].get("seq")) for e in events[:first_final]])
    finally:
        backend.release.set()
        await client.aclose()
        live.stop()


asyncio.run(main())
