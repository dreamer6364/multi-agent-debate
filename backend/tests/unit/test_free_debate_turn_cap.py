"""ISS-014 B-2：自由辩回合上限——真模型短句不再让赛程冗长。

根因：字数池按实际字数扣减，GLM 每轮输出偏短（几十字）→ 池消耗慢 →
旧 `guard<96` 让自由辩最多打 96 回合，整场 100+ 发言、40 分钟未收敛。
修复：`procedure.free_debate_max_turns`（每方上限，默认 8）与字数池
双约束，谁先到谁收敛；FakeLLM 长句仍靠字数池（2~4 回合），不受影响。
"""
from __future__ import annotations

from types import SimpleNamespace

from debate.core.config import AppConfig
from debate.engine.llm.gateway import LLMResult
from debate.engine.nodes.phases import make_free_node
from debate.engine.runtime import DebateContext
from debate.engine.schema import Camp, DebateFormat, Topic
from debate.engine.sim.digest import Digest

_NORMAL = "我方认为应当如此，因为对方论据存在明显漏洞，需正面回应。"

# 每轮只回约 25 字（模拟 GLM 短句）——字数池 1320/方需 ~52 回合才耗尽，
# 若无回合上限会打满旧 guard=96；有上限则 8 回合/方即收敛。
_SHORT = "短期看有风险。"


def _short_ctx(*, max_turns: int = 8, free_minutes: float = 6.0):
    cfg = AppConfig()
    cfg.procedure.free_debate_max_turns = max_turns
    cfg.procedure.phases.free_debate = free_minutes
    cfg.tools.elenchus.enabled = False

    def complete(messages, **kw):
        return LLMResult(text=_SHORT, prompt_tokens=10, completion_tokens=10)

    gateway = SimpleNamespace(backend=SimpleNamespace(), complete=complete)
    ctx = DebateContext(
        config=cfg,
        topic=Topic(topic_id="t", title="人工智能时代应更依赖技术还是更保持警惕",
                    pro_stance="更依赖技术", con_stance="更保持警惕",
                    pro_points=["技术提升效率"], con_points=["需警惕风险"]),
        agents={
            f"{side}-{i}": SimpleNamespace(camp=camp, system_prompt="sys")
            for side, camp in (("pro", Camp.PRO), ("con", Camp.CON)) for i in (1, 2, 3, 4)
        },
        gateway=gateway, compressor=lambda s: {}, format=DebateFormat.LITE,
        evidence={Camp.PRO: [], Camp.CON: []},
    )
    return ctx


def _free_node_speech_count(ctx) -> int:
    node = make_free_node(ctx)
    state = {"transcript": [], "digest": Digest()}
    result = node(state)
    return len(result["transcript"])


def test_short_speeches_converge_at_per_camp_turn_cap():
    """短句输出下，自由辩发言数受每方回合上限约束（不再打满旧 96）。"""
    ctx = _short_ctx(max_turns=8, free_minutes=6.0)
    n = _free_node_speech_count(ctx)
    # 每方 ≤8 回合 → 自由辩发言 ≤16；远小于旧上限 96
    assert n <= 16, f"自由辩发言 {n} 条，未受回合上限约束"
    assert n >= 2  # 至少双方各发言一次


def test_turn_cap_is_configurable():
    """回合上限可配：调小则发言更少。"""
    ctx8 = _short_ctx(max_turns=8)
    ctx3 = _short_ctx(max_turns=3)
    n8 = _free_node_speech_count(ctx8)
    n3 = _free_node_speech_count(ctx3)
    assert n3 <= 6
    assert n3 < n8


def test_long_speeches_still_converge_by_word_pool():
    """长句（FakeLLM 风格）仍靠字数池收敛，回合上限不干扰。"""
    ctx = _short_ctx(max_turns=8, free_minutes=1.0)  # 池 220/方，短打上限 110

    def complete(messages, **kw):
        return LLMResult(text="这是一个相对完整的长句论点。" * 12, prompt_tokens=10, completion_tokens=10)

    ctx.gateway.complete = complete
    n = _free_node_speech_count(ctx)
    # 每轮 ~160 字被硬截到 110 → 每方 2 回合耗尽 220 字池；回合上限 8 不触发
    assert 2 <= n <= 4, f"长句场景应靠字数池收敛，实际 {n} 条"
