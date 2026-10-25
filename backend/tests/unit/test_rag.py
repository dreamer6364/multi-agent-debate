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


def test_websearch_silent_degradation_and_provider_resolution(monkeypatch):
    """联网搜索（v1.4.1）：provider 解析 + 任何异常静默返回空（不阻塞比赛）。"""
    from debate.engine.rag import websearch as ws

    monkeypatch.setenv("SEARCH_PROVIDER", "zhipu")
    assert ws.resolve_provider() == "zhipu"
    monkeypatch.delenv("SEARCH_PROVIDER")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert ws.resolve_provider() == "none"
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-x")
    assert ws.resolve_provider() == "tavily"

    assert ws.web_search("q", provider="none") == []
    assert ws.web_search("q", provider="不存在") == []

    def boom(query, top_k):
        raise RuntimeError("网络炸了")

    monkeypatch.setitem(ws._PROVIDERS, "tavily", boom)
    assert ws.web_search("q", provider="tavily") == []  # 静默降级

    def no_ddgs(query, top_k):
        raise ImportError("ddgs 未安装")

    monkeypatch.setitem(ws._PROVIDERS, "ddgs", no_ddgs)
    assert ws.web_search("q", provider="ddgs") == []  # 可选依赖缺失也静默


def test_websearch_cards_adapter():
    from debate.engine.rag.websearch import WebEvidence, _to_card
    from debate.engine.rag.retriever import render_evidence

    card = _to_card(WebEvidence(
        title="AI 依赖风险研究", url="https://example.com/paper/abc",
        snippet="研究发现过度依赖导致技能退化……"))
    assert card.source == "网络检索 · example.com"
    assert card.source_type == "网络"
    assert "URL: https://example.com/paper/abc" in card.text
    text = render_evidence([card])
    assert "【证据参考】" in text and "网络检索 · example.com" in text


def test_websearch_merges_into_runner_evidence(monkeypatch, assets_root):
    """runner 预检索：网络结果并入本地证据（runner 级 mock，不真联网）。"""
    from debate.core.config import AppConfig, load_config
    from debate.engine.agents.loader import load_topics
    from debate.engine.llm.gateway import Gateway
    from debate.engine.rag.corpus import EvidenceCard
    from debate.engine.runner import run_debate
    from debate.engine.schema import DebateFormat
    from tests.fakes.compressor import fake_columns
    from tests.fakes.llm import FakeLLM

    captured: list[str] = []

    def fake_search(query, top_k=3, provider=None):
        captured.append(query)
        return [EvidenceCard(card_id="web", title="网络结果", source="网络检索 · test.com",
                             source_type="网络", text="网络检索到的论据。")]

    monkeypatch.setattr("debate.engine.runner.search_as_cards", fake_search)
    cfg = load_config(assets_root / "config.yaml").model_dump()
    cfg["procedure"]["phases"]["free_debate"] = 1.0
    cfg["web_search"]["provider"] = "none"  # 绕过 env 探测，注入 mock 直接生效
    config = AppConfig.model_validate(cfg)

    gw = Gateway(backend=FakeLLM.scripted(assets_root / "tests" / "fakes" / "script_lite.json"),
                 model_debater="fake", model_official="fake", max_retries=1, sleep=lambda s: None)
    result = run_debate(topic=load_topics(assets_root)[0], fmt=DebateFormat.LITE, config=config,
                        gateway=gw, compressor=fake_columns, assets_root=assets_root)
    assert result.status == "finished"
    assert len(captured) == 2  # 正反双方各一次检索
