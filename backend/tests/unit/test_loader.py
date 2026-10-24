"""Prompt 资产加载单测（TEST_PLAN UT-LOD-01~04，SPEC §7 / AGENT_CONSTRAINTS §8）。"""
import pytest

from debate.engine.agents.loader import PromptAssetError, load_agents, load_topics

FIVE_SECTIONS = "\n\n".join(
    f"## {name}\n内容" for name in ("身份", "职责", "行为规则", "语言风格", "禁止事项")
)


def test_load_all_agents_from_real_assets(assets_root):
    agents = load_agents(assets_root)
    assert len(agents) == 13
    assert agents["pro-1"].camp.value == "pro"
    assert agents["judge-logic"].camp.value == "neutral"
    assert "判准" in agents["pro-1"].system_prompt
    assert agents["timekeeper"].llm is False  # 计时员非 LLM（AGENT_CONSTRAINTS §2）
    assert agents["chair"].llm is True


def test_conduct_is_prefix_of_system_prompt(assets_root):
    prompt = load_agents(assets_root)["pro-2"].system_prompt
    joiner = prompt.index("\n\n---\n\n")
    assert "立场锁定" in prompt[:joiner]  # conduct 在前
    assert "驳论" in prompt[joiner:]  # 角色文件在后


def test_missing_section_fails_fast(tmp_path):
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "conduct.md").write_text("# 守则\n立场锁定。", encoding="utf-8")
    (tmp_path / "roles").mkdir()
    broken = "# 正方一辩\n\n" + FIVE_SECTIONS.replace("## 禁止事项\n内容\n\n", "").replace("## 禁止事项\n内容", "")
    (tmp_path / "roles" / "pro-1.md").write_text(broken, encoding="utf-8")
    with pytest.raises(PromptAssetError):
        load_agents(tmp_path, role_ids=["pro-1"])


def test_missing_keyword_fails_fast(tmp_path):
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "conduct.md").write_text("# 守则\n立场锁定。", encoding="utf-8")
    (tmp_path / "roles").mkdir()
    no_keyword = FIVE_SECTIONS.replace("判准", "标准")  # pro-1 义务关键词缺失
    (tmp_path / "roles" / "pro-1.md").write_text("# 正方一辩\n\n" + no_keyword, encoding="utf-8")
    with pytest.raises(PromptAssetError):
        load_agents(tmp_path, role_ids=["pro-1"])


def test_load_topics_from_real_assets(assets_root):
    topics = load_topics(assets_root)
    assert len(topics) == 3
    first = topics[0]
    assert first.topic_id == "topic-01"
    assert "人工智能" in first.title
    assert first.pro_points and first.con_points
    assert "更依赖技术" in first.pro_stance
    assert "保持警惕" in first.con_stance
