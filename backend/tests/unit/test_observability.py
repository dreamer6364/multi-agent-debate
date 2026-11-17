"""ISS-014 A-5：trace_id 贯通 + 结构化 LLM 调用日志（SPEC §6.2 / §7.4）。"""
from __future__ import annotations

import json
import logging

import pytest

from debate.engine.llm.gateway import Gateway
from debate.engine.llm.observability import log_llm_call, new_trace_id
from debate.engine.runner import run_debate
from debate.engine.schema import DebateFormat, Topic
from tests.fakes.compressor import fake_columns

MSG = [{"role": "user", "content": "x"}]


class _Backend:
    def __init__(self, *, fail: bool = False):
        self.fail = fail

    def complete(self, messages, **kwargs):
        if self.fail:
            raise RuntimeError("boom")
        return "正常发言"


# ---- trace_id 生成/传播 ----

def test_new_trace_id_is_short_and_unique():
    ids = {new_trace_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(len(t) == 12 and int(t, 16) >= 0 for t in ids)


def test_run_debate_injects_trace_id_into_gateway_and_result(assets_root, backend_root):
    from debate.core.config import load_config

    tid = "abcdef123456"
    gateway = Gateway(backend=_Backend(), model_debater="d", model_official="o", max_retries=0)
    result = run_debate(
        topic=Topic(topic_id="t", title="测试辩题 A 还是 B", pro_stance="A", con_stance="B",
                    pro_points=["A 好"], con_points=["B 好"]),
        fmt=DebateFormat.LITE, config=load_config(backend_root / "config.yaml"),
        gateway=gateway, compressor=fake_columns, assets_root=assets_root, trace_id=tid,
    )
    assert result.trace_id == tid
    assert gateway.trace_id == tid


def test_ctx_emit_injects_trace_id():
    from debate.core.config import AppConfig
    from debate.engine.runtime import DebateContext
    from debate.engine.schema import Topic

    ctx = DebateContext(
        config=AppConfig(),
        topic=Topic(topic_id="t", title="x", pro_stance="A", con_stance="B",
                    pro_points=["a"], con_points=["b"]),
        agents={}, gateway=Gateway(), compressor=lambda s: {}, format=DebateFormat.LITE,
        trace_id="traceme1234",
    )
    events = []
    ctx.on_event = events.append
    ctx.emit("phase_start", phase=1, name="引题")
    assert events[0]["data"]["trace_id"] == "traceme1234"
    assert events[0]["data"]["phase"] == 1


# ---- 结构化日志字段 ----

def test_log_llm_call_emits_json_without_payload(caplog):
    with caplog.at_level(logging.INFO, logger="debate.llm"):
        log_llm_call(trace_id="abc", tier="debater", model="gpt-x", attempt=1, ok=False,
                     latency_ms=12.34, error="RateLimitError")
    records = [json.loads(r.message) for r in caplog.records if r.name == "debate.llm"]
    assert len(records) == 1
    rec = records[0]
    assert rec["event"] == "llm_call" and rec["trace_id"] == "abc"
    assert rec["tier"] == "debater" and rec["model"] == "gpt-x" and rec["attempt"] == 1
    assert rec["ok"] is False and rec["error"] == "RateLimitError"
    assert rec["latency_ms"] == 12.3
    assert "messages" not in rec and "prompt" not in rec  # 不落 prompt/response 全文


def test_gateway_complete_logs_success_and_failure(caplog):
    gateway = Gateway(backend=_Backend(), model_debater="d", model_official="o",
                      max_retries=0, trace_id="trace42")
    with caplog.at_level(logging.INFO, logger="debate.llm"):
        gateway.complete(MSG, tier="debater", temperature=0.5, max_tokens=16)
    ok = [json.loads(r.message) for r in caplog.records if r.name == "debate.llm"]
    assert len(ok) == 1 and ok[0]["ok"] is True and ok[0]["trace_id"] == "trace42"
    assert ok[0]["prompt_tokens"] == 0 and "latency_ms" in ok[0]

    caplog.clear()
    gateway2 = Gateway(backend=_Backend(fail=True), model_debater="d", model_official="o",
                       max_retries=0, trace_id="trace42")
    with caplog.at_level(logging.INFO, logger="debate.llm"):
        with pytest.raises(Exception):
            gateway2.complete(MSG, tier="debater", temperature=0.5, max_tokens=16)
    bad = [json.loads(r.message) for r in caplog.records if r.name == "debate.llm"]
    assert len(bad) == 1 and bad[0]["ok"] is False and bad[0]["error"] == "RuntimeError"


def test_gateway_stream_logs_success(caplog):
    gateway = Gateway(backend=_Backend(), model_debater="d", model_official="o",
                      max_retries=0, trace_id="stream1")
    with caplog.at_level(logging.INFO, logger="debate.llm"):
        list(gateway.stream(MSG, tier="debater", temperature=0.5, max_tokens=16))
    records = [json.loads(r.message) for r in caplog.records if r.name == "debate.llm"]
    assert len(records) == 1 and records[0]["ok"] is True
    assert records[0]["completion_tokens"] > 0  # 流式按字符估算
