"""v1.8 辩手 Agent 工具循环单测：网关协议、工具执行、动态证据、风格轮换、降级回落。"""
from __future__ import annotations

from typing import Any

import pytest

from debate.engine.agents.context import build_debater_messages
from debate.engine.agents.loader import AgentSpec
from debate.engine.llm.gateway import Gateway, LLMResult
from debate.engine.runtime import DebateContext
from debate.engine.schema import Camp, DebateFormat, Speech, Topic
from debate.engine.tools.agent_tools import (
    build_tool_specs,
    execute_tool,
    run_debater_with_tools,
    tools_enabled_for,
)
from debate.core.config import AppConfig


# ---- 测试替身：支持工具协议的后端 ----

class ToolCallingBackend:
    """模拟 OpenAI function calling：第一轮返回 tool_calls，第二轮返回正文。"""

    def __init__(self, tool_name: str = "search_evidence", final_text: str = "最终发言正文"):
        self.tool_name = tool_name
        self.final_text = final_text
        self.rounds = 0
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def chat_with_tools(self, messages, *, tools, execute, tier, temperature, max_tokens, max_rounds):
        convo = list(messages)
        for _ in range(max_rounds + 1):
            self.rounds += 1
            if self.rounds == 1:
                # 返回"带 tool_calls 的 assistant 消息"由网关协议处理——此处直接模拟整轮语义
                args = {"query": "多任务 认知"}
                self.executed.append((self.tool_name, args))
                tool_result = execute(self.tool_name, args)
                assert isinstance(tool_result, str) and tool_result
                continue
            return LLMResult(text=self.final_text, prompt_tokens=10, completion_tokens=5)
        raise AssertionError("不可达")


class PlainBackend:
    """无工具协议的后端（FakeLLM 同类）：chat_with_tools 应回落 complete。"""

    def complete(self, messages, **kwargs) -> str:
        return "回落正文"


# ---- fixture ----

TOPIC = Topic(topic_id="t", title="A / B", pro_stance="支持A", con_stance="支持B",
              pro_points=["p1", "p2"], con_points=["q1"])
SPEC = AgentSpec(role_id="pro-1", camp=Camp.PRO, position_cn="正方一辩",
                 system_prompt="system", llm=True)


def make_ctx(gateway: Gateway, **kw) -> DebateContext:
    return DebateContext(
        config=AppConfig(), topic=TOPIC, agents={"pro-1": SPEC}, gateway=gateway,
        compressor=lambda speeches: {}, format=DebateFormat.LITE, **kw,
    )


def base_messages() -> list[dict[str, str]]:
    return build_debater_messages(
        spec=SPEC, topic=TOPIC, digest_text="", recent=[],
        instruction="任务", max_input_tokens=None,
    )


# ---- 网关协议 ----

def test_chat_with_tools_backend_delegation():
    backend = ToolCallingBackend()
    gw = Gateway(backend=backend)
    result = gw.chat_with_tools(
        base_messages(), tools=[], execute=lambda n, a: "工具结果",
        tier="debater", temperature=0.85, max_tokens=100, max_rounds=2,
    )
    assert result.text == "最终发言正文"
    assert gw.usage["calls"] == 1


def test_chat_with_tools_falls_back_to_complete_without_protocol():
    backend = PlainBackend()
    gw = Gateway(backend=backend)
    result = gw.chat_with_tools(
        base_messages(), tools=[], execute=lambda n, a: "不该被调用",
        tier="debater", temperature=0.85, max_tokens=100, max_rounds=2,
    )
    assert result.text == "回落正文"


# ---- 工具执行 ----

def test_execute_tool_check_time():
    ctx = make_ctx(Gateway(backend=PlainBackend()))
    text = execute_tool(ctx, "check_time", {}, 350)
    assert "350" in text


def test_execute_tool_unknown_raises():
    ctx = make_ctx(Gateway(backend=PlainBackend()))
    with pytest.raises(ValueError):
        execute_tool(ctx, "no_such_tool", {}, 100)


def test_execute_tool_search_evidence_without_retriever():
    ctx = make_ctx(Gateway(backend=PlainBackend()))
    text = execute_tool(ctx, "search_evidence", {"query": "关键词"}, 100)
    assert "不可用" in text


def test_execute_tool_check_argument_disabled():
    ctx = make_ctx(Gateway(backend=PlainBackend()))
    ctx.config.tools.elenchus.enabled = False
    text = execute_tool(ctx, "check_argument", {"text": "这是一个足够长的论证文本，用来触发长度校验逻辑。"}, 100)
    assert "未启用" in text


def test_tool_specs_prune_web_and_elenchus():
    ctx = make_ctx(Gateway(backend=PlainBackend()))
    ctx.web_provider = "none"
    ctx.config.tools.elenchus.enabled = False
    names = {s["function"]["name"] for s in build_tool_specs(ctx)}
    assert "search_web" not in names
    assert "check_argument" not in names
    assert "search_evidence" in names


# ---- 启用条件与降级回落 ----

def test_tools_enabled_for_respects_phase_whitelist():
    ctx = make_ctx(Gateway(backend=None))
    assert tools_enabled_for(ctx, "statement") is True
    assert tools_enabled_for(ctx, "free_debate") is False  # 默认白名单外
    assert tools_enabled_for(ctx, None) is False


def test_tools_enabled_for_fake_backend_is_false():
    ctx = make_ctx(Gateway(backend=PlainBackend()))
    assert tools_enabled_for(ctx, "statement") is False


def test_run_debater_with_tools_fallback_on_backend_error():
    class BrokenBackend:
        def chat_with_tools(self, *a, **kw):
            raise RuntimeError("协议崩溃")

        def complete(self, messages, **kwargs) -> str:
            return "降级正文"

    ctx = make_ctx(Gateway(backend=BrokenBackend()))
    result, used = run_debater_with_tools(ctx, messages=base_messages(), instruction="t", remaining_words=200)
    assert used is False
    assert result.text == "降级正文"


# ---- 表达多样性 ----

def test_style_hint_rotates_per_speaker_turn_count():
    from debate.engine.nodes.phases import _style_hint
    ctx = make_ctx(Gateway(backend=PlainBackend()))
    ctx.speeches = [Speech(seq=1, phase=2, phase_name="开篇立论", speaker="pro-1",
                           camp=Camp.PRO, text="x", word_count=1)]
    h1 = _style_hint(ctx, "pro-1")
    ctx.speeches.append(Speech(seq=2, phase=2, phase_name="开篇立论", speaker="pro-1",
                               camp=Camp.PRO, text="y", word_count=1))  # 模拟该辩手又发言了一条
    h2 = _style_hint(ctx, "pro-1")
    h_other = _style_hint(ctx, "pro-2")
    assert h1 != h2  # 同一辩手逐条轮换
    assert "临场" in h1 and "禁止照搬" in h1  # 反模板约束注入
    assert h_other != h1  # 不同辩手起步点不同


def test_dynamic_evidence_falls_back_without_retriever():
    from debate.engine.nodes.phases import _dynamic_evidence
    card = {"x": 1}
    ctx = make_ctx(Gateway(backend=PlainBackend()))
    ctx.evidence = {Camp.PRO: [card], Camp.CON: []}
    assert _dynamic_evidence(ctx, SPEC, []) == [card]
