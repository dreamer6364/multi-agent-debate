"""BM25 证据检索（v1.4）：jieba 分词 + rank-bm25，纯本地零模型。

查询构造按阵营区分（辩题 + 己方命题），双方各自检索 top_k——
正反方天然检索到不同侧重的证据卡（立场信息隔离的 RAG 对应物）。
"""
from __future__ import annotations

import jieba
from rank_bm25 import BM25Okapi

from ..schema import Camp, Topic
from .corpus import EvidenceCard, load_corpus

# 中文高频虚词/连词（BM25 停用词）：本身不携带主题信息，作为查询词只会
# 凭文档普遍含该词而抬高无关卡的得分。仅收"查询中可能出现的通用虚词"，
# 不收实词（避免误伤"爱""技术"等主题词）。
_STOPWORDS = frozenset("与和的了吗呢啊呀吧之乎者也是在有及或对把被从向着就都还又很太更最")


def _tokenize(text: str) -> list[str]:
    # BM25 停用词：高频虚词无区分度，只在无关查询下贡献噪声分——
    # 曾因语料平均长度变化把"与"的分推过低分阈值，令"量子纠缠与黑洞蒸发"
    # 这类无关查询命中含"与"的社会卡（宁缺毋滥被虚词击穿）。查询与文档对称剔除。
    return [t for t in jieba.lcut(text) if t.strip() and t not in _STOPWORDS]


# 辩题域探测（v1.5：治跨域硬套——商业/科技理论不再被套到情感辩题）
_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "情感": ("爱", "恋", "婚", "感情", "亲密", "关系", "伴侣", "分手", "追求", "喜欢"),
    "科技": ("技术", "人工智能", "AI", "算法", "互联网", "数字", "机器人", "科学"),
    "社会": ("社会", "制度", "法律", "政策", "公共", "网络平台", "舆论", "实名"),
    "商业": ("创业", "企业", "市场", "商业", "管理", "产品", "竞争"),
    "心理": ("心理", "认知", "决策", "思维", "情绪", "偏误", "判断"),
}


def detect_topic_domain(topic_text: str) -> str:
    """按关键词命中数探测辩题域；未命中返回"通用"（全库检索）。"""
    scores = {d: sum(topic_text.count(k) for k in kws) for d, kws in _DOMAIN_KEYWORDS.items()}
    best = max(scores, key=lambda d: scores[d])
    return best if scores[best] > 0 else "通用"


class EvidenceRetriever:
    def __init__(self, cards: list[EvidenceCard]):
        if not cards:
            raise ValueError("证据库为空")
        self.cards = cards
        # 索引文本 = 标题 + 来源 + 正文（书名也参与匹配，便于按文献名检索）
        self._docs = [_tokenize(f"{c.title} {c.source} {c.text}") for c in cards]
        self._bm25 = BM25Okapi(self._docs)

    @classmethod
    def from_dir(cls, knowledge_dir) -> "EvidenceRetriever":
        return cls(load_corpus(knowledge_dir))

    def search(self, query: str, top_k: int = 3, topic_domain: str = "通用") -> list[EvidenceCard]:
        """BM25 + 域加权（同域 ×1.6、通用域不降权、跨域 ×0.25 且高分门槛）+ 低分阈值过滤。

        v1.5：分数经域加权后仍低于 min_score 的卡不注入——宁缺毋滥。
        v2.9.1：跨域闸门收紧（实测弱模型会被跨域证据带偏全场议题）——
        跨域卡不仅要打折（×0.25），加权重后还必须达到 2.0 的更高门槛才准注入。
        """
        min_score = 1.0
        cross_min = 2.0  # 跨域卡额外门槛：弱相关直接挡掉
        raw = self._bm25.get_scores(_tokenize(query))
        weighted: list[tuple[int, float]] = []
        for i, score in enumerate(raw):
            if score <= 0:
                continue
            card = self.cards[i]
            if card.domain == topic_domain:
                factor, floor = 1.6, min_score
            elif card.domain == "通用" or topic_domain == "通用":
                factor, floor = 1.0, min_score
            else:
                factor, floor = 0.25, cross_min
            adjusted = score * factor
            if adjusted >= floor:
                weighted.append((i, adjusted))
        weighted.sort(key=lambda pair: pair[1], reverse=True)
        return [self.cards[i] for i, _ in weighted[:top_k]]

    def query_for(self, topic: Topic, camp: Camp) -> str:
        """阵营查询：辩题 + 己方命题（不同阵营检索侧重天然不同）。"""
        stance = topic.stance_for(camp)
        return f"{topic.title} {stance}"

    def evidence_for(self, topic: Topic, camp: Camp, top_k: int = 3) -> list[EvidenceCard]:
        domain = detect_topic_domain(f"{topic.title} {topic.pro_stance} {topic.con_stance}")
        return self.search(self.query_for(topic, camp), top_k=top_k, topic_domain=domain)


def render_evidence(cards: list[EvidenceCard]) -> str:
    """注入上下文的证据段文本；空时不输出段落。"""
    if not cards:
        return ""
    lines = ["【证据参考】（可引用其观点与来源名，不得虚构卡外精确数据）"]
    lines += [
        f"- 《卡片》{c.title}｜来源：{c.source}｜{c.source_type}：{c.text}" for c in cards
    ]
    return "\n".join(lines)
