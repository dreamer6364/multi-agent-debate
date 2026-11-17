"""ISS-014 A-4：单赛场 token 硬上限（config.model.token_budget，ISS-014 P1）。

护栏语义：
- Gateway 每次调用前检查累计 usage，超限抛 TokenBudgetExceeded（DebateAborted 子类）；
- runner 复用既有熔断终态 → status=failed + error 明细（不静默截断、不新增终态）；
- 例外工厂未注入 budget（单测直构 Gateway）→ 默认 None = 不限，既有测试零影响。
"""
from __future__ import annotations

import pytest

from debate.core.config import AppConfig, load_config
from debate.engine.agents.loader import load_topics
from debate.engine.llm.gateway import Gateway, LLMResult
from debate.engine.runner import run_debate
from debate.engine.runtime import DebateAborted, TokenBudgetExceeded
from debate.engine.schema import DebateFormat
from tests.fakes.compressor import fake_columns
from tests.unit.test_iss015_fallback import _ask_once, _ctx

MSG = [{"role": "user", "content": "x"}]


class _Tokenful:
    """每枪记账 150 prompt tokens 的普通后端（FakeLLM 不产 token，预算护栏碰不到它）。"""

    def __init__(self, *, text: str = "我方坚持本方立场，辩题关乎技术依赖，应当审慎权衡。"):
        self.calls = 0
        self.text = text

    def complete(self, messages, **kwargs):
        self.calls += 1
        return LLMResult(text=self.text, prompt_tokens=150, completion_tokens=0)


def test_gateway_trips_budget_before_second_call():
    """首打通过并记 150 tokens；第二枪在打后端前就被拦，不重试不退避。"""
    backend = _Tokenful()
    waits: list[float] = []
    gateway = Gateway(backend=backend, model_debater="d", model_official="o",
                      token_budget=100, max_retries=3, sleep=waits.append)
    assert gateway.complete(MSG, tier="debater", temperature=0.5, max_tokens=64).text == backend.text
    with pytest.raises(TokenBudgetExceeded) as exc_info:
        gateway.complete(MSG, tier="debater", temperature=0.5, max_tokens=64)
    assert "token 预算超限" in str(exc_info.value)
    assert "100" in str(exc_info.value)
    assert backend.calls == 1  # 第二枪根本没打到后端
    assert waits == []  # 没进 3 次重试 + 退避


def test_stream_trips_budget_before_iterating():
    """流式路径同语义：预算已耗尽时连生成器都不消费。"""
    called: list[bool] = []

    def stream(messages, **kwargs):
        called.append(True)
        yield "不应出现"

    gateway = Gateway(backend=type("B", (), {"stream": staticmethod(stream)})(),
                      model_debater="d", model_official="o", token_budget=50)
    gateway.usage["prompt_tokens"] = 60  # 直接预置为已超限
    with pytest.raises(TokenBudgetExceeded):
        list(gateway.stream(MSG, tier="debater", temperature=0.5, max_tokens=64))
    assert called == []


def test_budget_disabled_when_none_or_zero():
    """None/0 表示不限：既有调用方（未注入 budget）行为零变化。"""
    for budget in (None, 0):
        backend = _Tokenful()
        gateway = Gateway(backend=backend, model_debater="d", model_official="o",
                          token_budget=budget, max_retries=2, sleep=lambda s: None)
        for _ in range(2):
            gateway.complete(MSG, tier="debater", temperature=0.5, max_tokens=64)
        assert backend.calls == 2
        assert gateway.usage["prompt_tokens"] == 300


def test_budget_exceeded_reuses_debate_aborted_terminal():
    """TokenBudgetExceeded 是 DebateAborted 子类 → runner 既有 failed+error 终态直接复用。"""
    exc = TokenBudgetExceeded("单赛场 token 预算超限")
    assert isinstance(exc, DebateAborted)


def test_ask_propagates_budget_exceeded_instead_of_placeholder():
    """辩手节点不吞成 LLM 降级占位、不静默截断——异常直达 runner。"""
    ctx, _ = _ctx(cards=[], mode="ok")

    def boom(messages, **kwargs):
        raise TokenBudgetExceeded("单赛场 token 预算超限")

    ctx.gateway.complete = boom
    with pytest.raises(TokenBudgetExceeded):
        _ask_once(ctx)
    assert ctx.degradation_streak == 0  # 预算终止 ≠ 调用失败降级，不计降级笔数


@pytest.fixture(scope="module")
def budget_config(backend_root):
    cfg = load_config(backend_root / "config.yaml").model_dump()
    cfg["procedure"]["phases"]["free_debate"] = 1.0
    cfg["tools"]["elenchus"]["enabled"] = False
    cfg["model"]["token_budget"] = 100  # 首枪记 150 → 第二枪之前必超限
    return AppConfig.model_validate(cfg)


def test_run_debate_fails_with_budget_error_e2e(assets_root, budget_config):
    """图层端到端：预算触顶 → 比赛 failed，error 明记 token 预算，早于任何静默截断。"""
    gateway = Gateway(backend=_Tokenful(), model_debater="d", model_official="o",
                      token_budget=100, max_retries=1, sleep=lambda s: None)
    result = run_debate(
        topic=load_topics(assets_root)[0], fmt=DebateFormat.LITE, config=budget_config,
        gateway=gateway, compressor=fake_columns, assets_root=assets_root,
    )
    assert result.status == "failed"
    assert result.error and "token 预算超限" in result.error
    assert result.usage["prompt_tokens"] == 150  # 只花掉首枪那一笔


def test_config_yaml_budget_matches_default(backend_root):
    """config.yaml 是运行时真值：与 AppConfig 默认值一致（防字段漂移）。"""
    assert load_config(backend_root / "config.yaml").model.token_budget == 2_000_000
    assert AppConfig().model.token_budget == 2_000_000
