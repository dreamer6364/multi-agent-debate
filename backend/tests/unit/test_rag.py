"""RAG 证据检索测试（v1.4）：语料解析、BM25 相关性、六段注入与裁剪顺序。"""
import pytest

from debate.engine.agents.context import build_debater_messages
from debate.engine.agents.loader import load_agents, load_topics
from debate.engine.rag.corpus import CorpusError, parse_corpus
from debate.engine.rag.retriever import EvidenceRetriever, render_evidence
from debate.engine.schema import Camp

GOOD = """# 说明头（可自由文字）
# 证据卡：技术依赖
- 来源：《技术的本质》（阿瑟）
- 类型：书籍
技术组合进化，依赖即生态。

# 证据卡：匿名群体
- 来源：《乌合之众》（勒庞）
- 类型：书籍
匿名稀释责任，群体情绪传染。
"""


def test_parse_corpus_multi_cards_and_free_header():
    cards = parse_corpus(GOOD)
    assert len(cards) == 2
    assert cards[0].title == "技术依赖" and cards[0].source == "《技术的本质》（阿瑟）"
    assert cards[1].source_type == "书籍"


def test_parse_corpus_rejects_incomplete_card():
    bad = "# 证据卡：缺来源\n- 类型：书籍\n只有正文没有来源。\n"
    with pytest.raises(CorpusError):
        parse_corpus(bad)


def test_retriever_relevance_and_camp_query(assets_root):
    retriever = EvidenceRetriever.from_dir(assets_root / "knowledge")
    # 相关查询命中主题卡
    hits = retriever.search("人工智能 依赖 技术 风险", top_k=3)
    assert hits and all(h.source for h in hits)
    # 阵营查询：正反双方各自可检索（结果可不同，但都非空）
    topic = load_topics(assets_root)[0]
    pro = retriever.evidence_for(topic, Camp.PRO, top_k=3)
    con = retriever.evidence_for(topic, Camp.CON, top_k=3)
    assert pro and con


def test_render_evidence_format():
    cards = EvidenceRetriever.from_dir(
        __import__("pathlib").Path("knowledge") if __import__("pathlib").Path("knowledge").exists()
        else __import__("pathlib").Path("backend/knowledge")
    ).search("技术", top_k=2)
    text = render_evidence(cards)
    assert text.startswith("【证据参考】")
    assert "来源：" in text and "｜" in text


def test_context_injects_evidence_and_trims_last(assets_root):
    agents = load_agents(assets_root)
    topic = load_topics(assets_root)[0]
    ev = "【证据参考】（可引用其观点与来源名）\n- 《卡片》测试卡｜来源：《测试书》｜书籍：测试正文。"
    messages = build_debater_messages(
        spec=agents["pro-1"], topic=topic, digest_text="", recent=[],
        instruction="立论", evidence_text=ev,
    )
    content = messages[1]["content"]
    # 六段顺序：立场 → 证据 → 摘要 → 任务
    assert "【证据参考】" in content
    assert content.index("【本方立场】") < content.index("【证据参考】") < content.index("【战局摘要】")
    # 极限预算下证据段最后被裁（此前裁掉 recent/digest/points）
    tight = build_debater_messages(
        spec=agents["pro-1"], topic=topic, digest_text="摘" * 500,
        recent=[], instruction="立论", evidence_text=ev, max_input_tokens=260,
    )
    assert "【证据参考】" not in tight[1]["content"]  # 证据段最后裁（v1.4）
