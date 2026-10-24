"""Markdown 导出单测（TEST_PLAN UT-RND-01/02，SPEC §8 / CONSTRAINTS F5）。"""
from debate.core.config import load_config
from debate.engine.render.markdown import render_scorecard_md, render_transcript_md, role_display
from debate.engine.schema import Camp, DebateFormat, ScoreCard, Speech, Verdict


def _speech(seq, phase, name, speaker, camp, text, notes=None, interrupted=False):
    return Speech(seq=seq, phase=phase, phase_name=name, speaker=speaker, camp=camp,
                  text=text, side_notes=notes or [], interrupted=interrupted,
                  word_count=len(text))


def _result():
    transcript = [
        _speech(1, 2, "开篇立论", "pro-1", Camp.PRO, "我方立论如下。",
                notes=["计时员：还剩 30 秒", "计时员：时间到"]),
        _speech(2, 2, "开篇立论", "con-1", Camp.CON, "对方定义回避风险。"),
        _speech(3, 3, "质询一辩", "pro-1", Camp.PRO, "被打断前的一半回答。", interrupted=True,
                notes=["质询方打断：请正面回答"]),
    ]
    scorecards = [
        ScoreCard(judge="judge-logic", focus="logic",
                  pro_scores={"statement": 20, "rebuttal_cross": 15, "attack": 10, "free": 15, "closing": 10, "conduct": 5},
                  con_scores={"statement": 18, "rebuttal_cross": 14, "attack": 12, "free": 14, "closing": 11, "conduct": 4},
                  vote=Camp.PRO, best_speaker="pro-1",
                  highlights={Camp.PRO: ["“判准”清晰"], Camp.CON: ["“安全阀”贴切"]},
                  weaknesses={Camp.PRO: ["“可控”缺证"], Camp.CON: ["“量化”未给"]}),
    ]
    verdict = Verdict(votes={Camp.PRO: 1, Camp.CON: 0},
                      team_totals={Camp.PRO: 75.0, Camp.CON: 73.0},
                      winner=Camp.PRO, best_speaker="pro-1", decisive_rule="majority")
    from debate.engine.runner import DebateResult

    return DebateResult(status="finished", transcript=transcript, scorecards=scorecards,
                        verdict=verdict, usage={"calls": 5, "prompt_tokens": 100, "completion_tokens": 50})


def test_role_display():
    assert role_display("pro-1") == "正方·一辩"
    assert role_display("con-3") == "反方·三辩"
    assert role_display("chair") == "主席"
    assert role_display("judge-logic") == "逻辑评委"


def test_transcript_md_format(assets_root):
    config = load_config(assets_root / "config.yaml")
    topic = type(config)  # 占位避免误用；真实 topic 用 SimpleNamespace
    from types import SimpleNamespace

    topic = SimpleNamespace(title="测试辩题：A / 非A")
    md = render_transcript_md(topic, DebateFormat.FULL, _result(), config)
    assert md.startswith("# 辩论实录：测试辩题：A / 非A")
    assert "## 【环节 2｜开篇立论】" in md and "时间 3 分钟" in md
    assert "## 【环节 3｜质询一辩】" in md and "每组 1.5 分钟" in md
    assert "**正方·一辩**：我方立论如下。" in md
    assert "*（计时员：还剩 30 秒）*" in md
    assert "（被计时截断）" in md
    assert "*（质询方打断：请正面回答）*" in md
    assert "- LLM 用量：5 次调用 / 150 tokens" in md
    assert "- 结果：正方获胜" in md


def test_scorecard_md_table(assets_root):
    from types import SimpleNamespace

    topic = SimpleNamespace(title="测试辩题")
    md = render_scorecard_md(topic, DebateFormat.FULL, _result())
    assert "| 评委 | 正方 | 反方 | 投票 |" in md
    assert "| 逻辑评委 | 75 | 73 | 正方 |" in md
    assert "| **结果** | 团体总分 75 | 团体总分 73 | 1 : 0 | **正方获胜** |" in md
    assert "最佳辩手：pro-1" in md
    assert "“判准”清晰" in md
