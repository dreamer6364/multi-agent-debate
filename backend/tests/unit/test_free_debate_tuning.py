"""v2.9.3 自由辩论调优回归：更长对攻、完整表达、反问句对抗性。"""
from __future__ import annotations

import inspect


def test_free_debate_budget_defaults():
    """时间池与单轮上限放宽：每队 6 分钟、单轮 1.5 分钟（330 字）。"""
    from debate.core.config import ProcedureConfig

    proc = ProcedureConfig()
    assert proc.phases.free_debate == 6.0
    assert proc.free_debate_turn_cap == 1.5
    from debate.engine.schema import Camp
    from debate.engine.sim.timer import FreeDebateClock

    clock = FreeDebateClock(
        minutes_per_camp=proc.phases.free_debate,
        speed_cpm=proc.speed_cpm,
        turn_cap_minutes=proc.free_debate_turn_cap,
    )
    assert clock.turn_cap_words == 330
    assert clock.pools[Camp.PRO].remaining == 1320


def test_free_node_instruction_demands_rhetorical_questions():
    """自由辩指令必须包含反问句对抗要求与完整表达要求。"""
    from debate.engine.nodes import phases

    src = inspect.getsource(phases.make_free_node)
    assert "反问句" in src
    assert "把话说完整" in src
    # 旧的压抑性措辞已移除
    assert "一次只打一个点" not in src
    assert "≤预算字数" not in src
