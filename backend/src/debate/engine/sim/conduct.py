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
# 提问短文本比对用的标点/空白归一化（P0-3 回应义务：判定"同一问题"）
_PUNCT = re.compile(r"[\s，。、；：！？,.;:!?\"'“”‘’（）()《》【】〈〉<>—…·/／|~～]+")


def _grams(text: str, n: int = 2) -> set[str]:
    """二元组集合（去标点）：短提问判定"同一问题"的兜底比对粒度。"""
    body = _PUNCT.sub("", text or "")
    return {body[i : i + n] for i in range(max(0, len(body) - n + 1))}


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


def is_repetitive(text: str, prior_texts: list[str], min_overlap: int = 25) -> bool:
    """复读检测（v1.5，P1-3 收紧至 25 字）：与此前任一发言存在 ≥min_overlap 字的连续相同片段。

    治"整场复读同一金句"：非整段重复（那是回避），而是大段论据复述。
    15 字在中文里只是半句话——回扣己方判准/定义的正当复述几乎必然命中，
    误伤率过高，故默认阈值提到 25 字（一句话量级）。
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


# ---- P2-4 数据诚信的代码侧线索（SPEC §4.6）：只抓"待查"线索，判定权在内容评委 ----
_PCT = re.compile(r"\d+(?:[.,]\d+)?\s*[%％]")
_CLAIM_CUE = re.compile(
    r"研究表明|研究显示|调查显示|据统计|数据显示|报告指出|报告发现|实证研究|大数据显示"
)
_SENT_SPLIT = re.compile(r"[。！？!?；;\n]")
_QUOTE_MAX = 24


def _source_tokens(source: str) -> list[str]:
    """证据来源 → 可在发言句中匹配的令牌（书名、主体名、作者/机构），长度 ≥2 才有效。"""
    tokens: list[str] = []
    tokens += re.findall(r"《([^》]+)》", source)                      # 书名/文献名
    head = re.split(r"[（(]", source, maxsplit=1)[0]                  # 括号前主体（如"世界银行年度报告"）
    if head.strip():
        tokens.append(head.strip())
    for paren in re.findall(r"[（(]([^）)]+)[）)]", source):          # 括号内的作者/机构
        tokens += [t for t in re.split(r"[,，、/\s]+", paren) if len(t) >= 2]
    whole = re.sub(r"[《》（）()\[\]【】\s]", "", source)
    if len(whole) >= 2:
        tokens.append(whole)
    return [t for t in tokens if len(t) >= 2]


def flag_unverified_claims(text: str, allowed_sources: list[str],
                           max_flags: int = 2) -> list[str]:
    """数据诚信线索抓取（P2-4，守则三/ G5 的代码侧兜底）。

    抓两类高信号表述：①精确百分比；②"研究表明/据统计"式研究结论引用——
    逐句检查：该句若**未标注任何平台白名单来源**（书名或作者名命中），
    旁注 `数据待查：「…」` 交内容评委核验。代码不下"造假"结论（SPEC §4.6：
    数据造假的判定权在评委），只把"值得查的句子"点出来，消除评委凭印象抽查的盲区。

    降噪：反问/质询对方数据的句子（含"对方"且以问号收尾）不抓——那是交锋不是举证。
    """
    if not text:
        return []
    tokens: list[str] = []
    for source in allowed_sources or []:
        tokens += _source_tokens(source)
    flags: list[str] = []
    for sentence in _SENT_SPLIT.split(text):
        s = sentence.strip()
        if not s or not (_PCT.search(s) or _CLAIM_CUE.search(s)):
            continue
        if ("对方" in s and s.endswith(("？", "?"))) or "对方辩友" in s:
            continue  # 反问对方的数据要求，不是己方举证
        if any(token in s for token in tokens):
            continue  # 已标注平台白名单来源（守则三允许直接引用）
        quote = s[:_QUOTE_MAX] + ("…" if len(s) > _QUOTE_MAX else "")
        flags.append(f"数据待查：「{quote}」")
        if len(flags) >= max_flags:
            break
    return flags


def same_question(new_q: str, prior_q: str, min_overlap: int = 10,
                  bigram_floor: float = 0.55) -> bool:
    """判定两条提问是否为"同一问题"的换皮追问（守则四：连续三次追问同一问题）。

    混合判定（P0-3 落地修订）：
    - 连续片段 ≥min_overlap 字 → 同一问题（几乎原样重复）；
    - 否则按二元组覆盖率：改写会破坏连续片段、但二元组大体保留，
      覆盖率 = |A∩B| / min(|A||B|) ≥ bigram_floor 判为同一问题。
    短提问（长度 < min_overlap）只走二元组覆盖率。
    """
    new_q, prior_q = new_q.strip(), prior_q.strip()
    if not new_q or not prior_q:
        return False
    if min(len(new_q), len(prior_q)) >= min_overlap and is_repetitive(new_q, [prior_q], min_overlap):
        return True
    a, b = _grams(new_q), _grams(prior_q)
    if not a or not b:
        return False
    return len(a & b) / max(1, min(len(a), len(b))) >= bigram_floor


class FollowupLedger:
    """回应义务计数器（守则四 / G6，SPEC §4.6）：质询与攻辩节点内按"同一问题"聚簇计数。

    L1 确定性实现：同一问题被追问达到 threshold 次、且该次回答是"硬逃避"
    （空回答或与此前回答逐字重复）→ 判定为"仍无对应回答"，由调用方旁注 `回避追问`。
    语义性回避（答非所问但不是复读）留给评委 rubric 判定，代码不下结论。
    """

    def __init__(self, threshold: int = 3, min_overlap: int = 10):
        self.threshold = threshold
        self.min_overlap = min_overlap
        # 每个问题簇：{"questions": [同题各次提问], "answers": [对应各次回答]}
        self._clusters: list[dict] = []

    def register_ask(self, question: str) -> int:
        """登记一次提问，返回该问题簇累计被追问的次数（含本次）。"""
        for cluster in self._clusters:
            if any(same_question(question, q, self.min_overlap) for q in cluster["questions"]):
                cluster["questions"].append(question)
                return len(cluster["questions"])
        self._clusters.append({"questions": [question], "answers": []})
        return 1

    def is_unanswered(self, answer_text: str, asks: int) -> bool:
        """第 threshold 次追问后，该回答是否构成"仍无对应回答"（硬逃避判定）。"""
        if asks < self.threshold:
            return False
        cluster = self._cluster_of(answer_text) or {}
        return is_hard_evasion(answer_text, cluster.get("answers", []))

    def register_answer(self, answer_text: str) -> None:
        """把一次回答挂到最近登记的问题簇上（供后续同题追问的复读判定）。"""
        if self._clusters:
            self._clusters[-1]["answers"].append(answer_text.strip())

    def _cluster_of(self, _answer_text: str) -> dict | None:
        return self._clusters[-1] if self._clusters else None
