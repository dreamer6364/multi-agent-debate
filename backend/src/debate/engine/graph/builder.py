"""图构建（ARCH §4.1）：full 10 节点 / lite 6 节点，顺序边；循环在节点内部（ARCH D1）。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..nodes import (
    make_attack_node,
    make_closing_node,
    make_cross_node,
    make_free_node,
    make_judging_node,
    make_opening_node,
    make_rebuttal_node,
    make_statement_node,
    make_summary_node,
    make_verdict_node,
)
from ..procedure import sequence_for
from ..runtime import DebateContext
from ..schema import DebateFormat
from .state import DebateState

_NODE_FACTORIES = {
    "opening": make_opening_node,
    "statement": make_statement_node,
    "cross_examination": make_cross_node,
    "rebuttal": make_rebuttal_node,
    "attack": make_attack_node,
    "attack_summary": make_summary_node,
    "free_debate": make_free_node,
    "closing": make_closing_node,
    "judging": make_judging_node,
    "verdict": make_verdict_node,
}


def build_graph(ctx: DebateContext, fmt: DebateFormat):
    sequence = sequence_for(fmt)
    builder = StateGraph(DebateState)
    for key in sequence:
        builder.add_node(key, _NODE_FACTORIES[key](ctx))
    builder.add_edge(START, sequence[0])
    for current, nxt in zip(sequence, sequence[1:]):
        builder.add_edge(current, nxt)
    builder.add_edge(sequence[-1], END)
    return builder.compile()
