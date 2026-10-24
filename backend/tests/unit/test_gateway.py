"""LLM 网关单测：重试次数、耗尽异常、用量统计（TEST_PLAN FT-LLM 前置，SPEC §7）。"""
import pytest

from debate.engine.llm.gateway import Gateway, LLMExhausted
from tests.fakes.llm import FakeLLM

MSG = [{"role": "user", "content": "{repeat:7}"}]


def test_gateway_wraps_fake_and_counts_usage():
    gateway = Gateway(backend=FakeLLM(mode="echo"), model_debater="d", model_official="o",
                      max_retries=2, sleep=lambda s: None)
    result = gateway.complete(MSG, tier="debater", temperature=0.5, max_tokens=64)
    assert result.text == "字" * 7
    assert gateway.usage["calls"] == 1


def test_gateway_retries_then_exhausts():
    fake = FakeLLM(mode="fault", error_kind="http_500")
    gateway = Gateway(backend=fake, model_debater="d", model_official="o",
                      max_retries=2, sleep=lambda s: None)
    with pytest.raises(LLMExhausted):
        gateway.complete(MSG, tier="debater", temperature=0.5, max_tokens=64)
    assert len(fake.calls) == 3  # 首次 1 + 重试 2（SPEC §7：重试 3 次耗尽则占位降级）


def test_gateway_recovers_after_transient_failure():
    class Flaky:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("瞬态故障")
            return "恢复"

    gateway = Gateway(backend=Flaky(), model_debater="d", model_official="o",
                      max_retries=3, sleep=lambda s: None)
    assert gateway.complete(MSG, tier="judge", temperature=0.3, max_tokens=64).text == "恢复"


def test_stream_accumulates_and_counts_usage():
    fake = FakeLLM(mode="echo")
    gateway = Gateway(backend=fake, model_debater="d", model_official="o",
                      max_retries=2, sleep=lambda s: None)
    chunks = list(gateway.stream([{"role": "user", "content": "{repeat:9}"}],
                                 tier="debater", temperature=0.5, max_tokens=64))
    assert "".join(chunks) == "字" * 9
    assert gateway.usage["calls"] == 1
    assert gateway.usage["completion_tokens"] == 9


def test_stream_notifies_attempt_and_resets_on_retry():
    class FlakyStream:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            return "不应走到这里"

        def stream(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("首次流失败")
            yield "恢"
            yield "复"

    backend = FlakyStream()
    gateway = Gateway(backend=backend, model_debater="d", model_official="o",
                      max_retries=2, sleep=lambda s: None)
    attempts: list[int] = []
    chunks = list(gateway.stream([{"role": "user", "content": "x"}], tier="debater",
                                 temperature=0.5, max_tokens=64,
                                 on_attempt=attempts.append))
    assert "".join(chunks) == "恢复"
    assert attempts == [0, 1]  # 重试时 attempt 递增（前端据此重置累积）


def test_stream_exhausts_after_retries():
    gateway = Gateway(backend=FakeLLM(mode="fault", error_kind="http_500"),
                      model_debater="d", model_official="o",
                      max_retries=2, sleep=lambda s: None)
    with pytest.raises(LLMExhausted):
        list(gateway.stream([{"role": "user", "content": "x"}],
                            tier="debater", temperature=0.5, max_tokens=64))
