"""守则违规的代码侧检测（SPEC §4.6 / AGENT_CONSTRAINTS §7）。

L1 规则归代码（CONSTRAINTS B4）：
- 人身攻击 / 元话语：词表命中；
- markdown 标题与代码块：G8 输出纯度，直接剥离；
- 硬逃避：空回答或逐字重复此前回答；语义性逃避由评委判定，代码不下结论。
"""
from __future__ import annotations

import re

_MARKER = re.compile(r"[【\[]\s*打断\s*[】\]][^。！？!?；;\n]*[。！？!?；;\n]?")
_PLAIN_MARKER = re.compile(r"[【\[]\s*打断\s*[】\]]\s*")
# 舞台标记（如 [自由辩论]）：G8 输出纯度，模型偶发把环节名写成标签前缀
_STAGE_TAG = re.compile(r"^\s*[【\[][^【\】\]]{1,8}[\]】]\s*")


def strip_stage_tags(text: str) -> str:
    while True:
        new = _STAGE_TAG.sub("", text)
        if new == text:
            return new.strip()
        text = new
NOTE_ABUSE = "违规：人身攻击"
NOTE_META = "违规：跳出角色"
NOTE_EVASION = "回避追问"

# G1 元话语特征（AGENT_CONSTRAINTS §3）
_META_PHRASES = (
    "作为AI",
    "作为AI助手",
    "作为人工智能",
    "作为语言模型",
    "语言模型",
    "本模拟",
    "系统提示词",
    "训练数据",
)

_MD_HEADING = re.compile(r"^#{1,6}\s")
_FENCE = "```"


def has_interrupt_marker(text: str) -> bool:
    return bool(_MARKER.search(text))


def strip_interrupt_marker(text: str) -> str:
    """非质询环节的输出纯度：守则六限定打断只存在于质询环节。

    只剥离 [打断] 标记 token 本身、保留其后的发言内容——
    模型常把整段论证写在标记后，若连内容一起剥会产出空发言。
    """
    return _PLAIN_MARKER.sub("", text).strip()


def scan(text: str, abuse_keywords: list[str]) -> list[str]:
    """扫描一条发言，返回触发的违规旁注（可能为空）。"""
    notes: list[str] = []
    if any(k and k in text for k in abuse_keywords):
        notes.append(NOTE_ABUSE)
    if any(p in text for p in _META_PHRASES):
        notes.append(NOTE_META)
    return notes


def strip_forbidden_format(text: str) -> tuple[str, bool]:
    """剥离 markdown 标题行与代码块（含围栏内内容）。

    返回 (清洗文本, 是否发生剥离)。
    """
    out_lines: list[str] = []
    in_fence = False
    changed = False
    for line in text.splitlines():
        if line.lstrip().startswith(_FENCE):
            in_fence = not in_fence
            changed = True
            continue
        if in_fence:
            changed = True
            continue
        if _MD_HEADING.match(line):
            changed = True
            continue
        out_lines.append(line)
    return "\n".join(out_lines).strip(), changed


def is_hard_evasion(answer_text: str, previous_answers: list[str]) -> bool:
    """L1 硬逃避：空回答或与此前任一回答逐字重复。"""
    text = answer_text.strip()
    return (not text) or any(text == previous.strip() for previous in previous_answers)
