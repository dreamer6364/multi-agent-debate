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

_MD_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]*")
_FENCE = "```"
# 行首列表符号（- / * / •）——保留文本，只去掉符号
_MD_BULLET = re.compile(r"^[ \t]*[-*•·][ \t]+", re.M)
# 成对强调：**加粗**（保留，前端渲染 <strong>）；__下划线__ 一律降级为纯文本
_MD_BOLD_PAIR = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)
_MD_UNDER_PAIR = re.compile(r"__(?=\S)(.+?)(?<=\S)__", re.S)
# 私有区哨兵：保护成对的 ** 不被后续的"清星号"误伤
_SENT_O, _SENT_C = "\ue000", "\ue001"


def strip_markdown_noise(text: str) -> str:
    """清掉正文里的 markdown 噪声，治"发言里冒出裸 * "的观感问题。

    口径：成对的 ``**加粗**`` 保留（前端按 markdown-lite 渲染为粗体）；
    落单的 ``*``、成对的 ``__``、反引号、删除线、行首列表符号、``#`` 标题前缀全部剥掉，
    只留可读正文。输出对纯文本渲染安全（前端不再需要容错裸符号）。
    """
    if not text:
        return text
    text = _MD_BOLD_PAIR.sub(lambda m: f"{_SENT_O}{m.group(1)}{_SENT_C}", text)
    text = text.replace("*", "")          # 落单星号 / 未闭合的 ** 一并清掉
    text = _MD_UNDER_PAIR.sub(r"\1", text)
    text = text.replace("`", "").replace("~~", "")
    text = _MD_BULLET.sub("", text)
    text = _MD_HEADING.sub("", text)
    return text.replace(_SENT_O, "**").replace(_SENT_C, "**")


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
    """剥离 markdown 标题/代码块/强调符号，只留可读正文。

    v2.2：标题行不再整行丢弃（此前 ``### 定义`` 会连内容一起消失），改为去掉 ``#`` 前缀；
    同时统一清掉落单星号、反引号等噪声——正文绝不会再出现裸 ``*``（前端只渲染成对的加粗）。
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
        out_lines.append(line)
    cleaned = strip_markdown_noise("\n".join(out_lines).strip())
    return cleaned, changed or cleaned != text.strip()


def is_repetitive(text: str, prior_texts: list[str], min_overlap: int = 15) -> bool:
    """复读检测（v1.5）：与此前任一发言存在 ≥min_overlap 字的连续相同片段。

    治"整场复读同一金句"：非整段重复（那是回避），而是大段论据复述。
    实现：text 的定长子串集合 × prior 滑窗匹配（发言 ≤400 字，规模安全）。
    """
    if len(text) < min_overlap:
        return False
    subs = {text[i : i + min_overlap] for i in range(len(text) - min_overlap + 1)}
    for prior in prior_texts:
        for j in range(len(prior) - min_overlap + 1):
            if prior[j : j + min_overlap] in subs:
                return True
    return False


def is_hard_evasion(answer_text: str, previous_answers: list[str]) -> bool:
    """L1 硬逃避：空回答或与此前任一回答逐字重复。"""
    text = answer_text.strip()
    return (not text) or any(text == previous.strip() for previous in previous_answers)
