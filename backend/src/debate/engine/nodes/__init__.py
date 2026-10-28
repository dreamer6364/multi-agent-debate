"""环节节点工厂（ARCH D1：图节点 = 环节，环节内对话往返为节点内命令式循环）。"""
from .phases import (
    make_attack_node,
    make_closing_node,
    make_cross_node,
    make_free_node,
    make_rebuttal_node,
    make_statement_node,
    make_summary_node,
)
from .officials import make_closing_ceremony_node, make_opening_node, make_verdict_node
from .judges import make_judging_node

__all__ = [
    "make_opening_node", "make_statement_node", "make_cross_node", "make_rebuttal_node",
    "make_attack_node", "make_summary_node", "make_free_node", "make_closing_node",
    "make_judging_node", "make_verdict_node", "make_closing_ceremony_node",
]
