"""BM25 证据检索（v1.4）：jieba 分词 + rank-bm25，纯本地零模型。

查询构造按阵营区分（辩题 + 己方命题），双方各自检索 top_k——
正反方天然检索到不同侧重的证据卡（立场信息隔离的 RAG 对应物）。
"""
from __future__ import annotations

import jieba
from rank_bm25 import BM25Okapi

from ..schema import Camp, Topic
from .corpus import EvidenceCard, load_corpus


def _tokenize(text: str) -> list[str]:
    return [t for t in jieba.lcut(text) if t.strip()]


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

    def search(self, query: str, top_k: int = 3) -> list[EvidenceCard]:
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(range(len(self.cards)), key=lambda i: scores[i], reverse=True)
        return [self.cards[i] for i in ranked[:top_k] if scores[i] > 0]

    def query_for(self, topic: Topic, camp: Camp) -> str:
        """阵营查询：辩题 + 己方命题（不同阵营检索侧重天然不同）。"""
        stance = topic.stance_for(camp)
        return f"{topic.title} {stance}"

    def evidence_for(self, topic: Topic, camp: Camp, top_k: int = 3) -> list[EvidenceCard]:
        return self.search(self.query_for(topic, camp), top_k=top_k)


def render_evidence(cards: list[EvidenceCard]) -> str:
    """注入上下文的证据段文本；空时不输出段落。"""
    if not cards:
        return ""
    lines = ["【证据参考】（可引用其观点与来源名，不得虚构卡外精确数据）"]
    lines += [
        f"- 《卡片》{c.title}｜来源：{c.source}｜{c.source_type}：{c.text}" for c in cards
    ]
    return "\n".join(lines)
