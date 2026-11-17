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
    # v1.1：pro-1 关键词为 ("判准", "检索")——文本补上"检索"，只缺"判准"，精确验证该条校验
    no_keyword = FIVE_SECTIONS.replace("判准", "标准") + "\n工具取材：检索证据。"
    (tmp_path / "roles" / "pro-1.md").write_text("# 正方一辩\n\n" + no_keyword, encoding="utf-8")
    with pytest.raises(PromptAssetError):
        load_agents(tmp_path, role_ids=["pro-1"])


def test_missing_tool_keyword_fails_fast(tmp_path):
    """v1.1（AGENT_CONSTRAINTS §8.4）：评委缺"没有检索工具"声明即 fail-fast。"""
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "conduct.md").write_text("# 守则\n立场锁定。", encoding="utf-8")
    (tmp_path / "roles").mkdir()
    body = FIVE_SECTIONS + "\n打分与点评。"  # 有"打分"，缺"没有检索工具"
    (tmp_path / "roles" / "judge-logic.md").write_text("# 逻辑评委\n\n" + body, encoding="utf-8")
    with pytest.raises(PromptAssetError, match="没有检索工具"):
        load_agents(tmp_path, role_ids=["judge-logic"])


def test_tool_strategy_written_for_every_role(assets_root):
    """v1.1：每角色文件必须写明工具口径——辩手差异化取材策略、评委/主席无工具声明。

    loader 关键词校验已 fail-fast 兜底，本测试显式固化意图（防有人把策略写成空话
    过了关键词检查却无实义）。
    """
    agents = load_agents(assets_root)
    debaters = ("pro-1", "pro-2", "pro-3", "pro-4", "con-1", "con-2", "con-3", "con-4")
    for rid in debaters:
        prompt = agents[rid].system_prompt
        assert "工具取材" in prompt, f"{rid} 缺工具取材策略"
        assert "search_evidence" in prompt or "check_argument" in prompt, f"{rid} 策略未落到具体工具"
    for rid in ("judge-logic", "judge-content", "judge-expression"):
        assert "没有检索工具" in agents[rid].system_prompt, f"{rid} 缺无工具声明"
        assert "摘录池" in agents[rid].system_prompt, f"{rid} 未说明引用范围"
    chair = agents["chair"].system_prompt
    assert "没有任何工具" in chair and "检索" in chair, "主席缺无工具声明"
    # 时间员是纯代码角色：无 LLM 调用，不要求工具策略（AGENT_CONSTRAINTS §2）


def test_load_topics_from_real_assets(assets_root):
    topics = load_topics(assets_root)
    assert len(topics) == 3
    first = topics[0]
    assert first.topic_id == "topic-01"
    assert "人工智能" in first.title
    assert first.pro_points and first.con_points
    assert "更依赖技术" in first.pro_stance
    assert "保持警惕" in first.con_stance
