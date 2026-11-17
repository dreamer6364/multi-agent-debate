"""A-1 评测体系单测：golden set 资产、judge 解析与降级、聚合数学、CLI dry-run。

不打真实 LLM——judge 调用经 stub gateway 注入（ISS-014 A-1）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from debate.engine.agents.loader import load_topics
from debate.engine.runner import DebateResult
from debate.engine.schema import Camp, Speech
from eval.dataset import GoldenItem, load_golden_set
from eval.judge import (
    DIMS,
    DimensionScore,
    JudgeParseError,
    JudgeScore,
    SideScore,
    judge_debate,
    parse_judge_output,
)
from eval.run_eval import aggregate, main, render_transcript

# ---- 测试替身 ----


class _StubGateway:
    """按调用次序回放预设文本的 judge 网关（第 n 次调用取第 n 条，越界取末条）。"""

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages, *, tier, temperature, max_tokens):
        self.calls.append(messages)
        assert tier == "judge" and temperature == 0.1  # judge 档 + 低随机（ISS-014 A-1 口径）
        idx = min(len(self.calls) - 1, len(self._replies) - 1)
        return SimpleNamespace(text=self._replies[idx])


def _valid_judge_json() -> str:
    return json.dumps(
        {
            "stance_adherence": {"pro": {"score": 8, "reason": "守住判准"}, "con": {"score": 7, "reason": "基本守住"}},
            "citation_integrity": {"pro": {"score": 6, "reason": "引用证据卡"} , "con": {"score": 9, "reason": "无编造"}},
            "argument_quality": {"pro": {"score": 7, "reason": "链条完整"}, "con": {"score": 5, "reason": "有滑坡"}},
        },
        ensure_ascii=False,
    )


_ITEM = GoldenItem(
    id="g01",
    domain="科技",
    source="builtin:topic-01",
    title="人工智能时代，人类应该更依赖技术 / 更保持警惕",
    pro_stance="人工智能时代，人类应该更依赖技术。",
    con_stance="人工智能时代，人类应该更保持警惕。",
    pro_points=["效率与普惠。"],
    con_points=["风险不可逆。"],
)


# ---- golden set 资产 ----


def test_golden_set_loads_and_fields_complete():
    items = load_golden_set()
    assert len(items) >= 10  # ISS-014 A-1：10 条起步
    ids = [item.id for item in items]
    assert len(ids) == len(set(ids)), "id 必须唯一"
    domains = {item.domain for item in items}
    assert len(domains) >= 7, f"领域覆盖过窄: {domains}"
    for item in items:
        assert item.title and item.pro_stance and item.con_stance
        assert item.pro_points and item.con_points


def test_builtin_golden_items_stay_in_sync_with_topics(assets_root):
    """source=builtin 的条目必须与 topics/*.md 逐字段一致（防两边漂移）。"""
    topics = {t.title: t for t in load_topics(assets_root)}
    builtins = [i for i in load_golden_set() if i.source.startswith("builtin")]
    assert len(builtins) == 3
    for item in builtins:
        topic = topics.get(item.title)
        assert topic is not None, f"内置辩题已不存在: {item.title}"
        assert item.pro_stance == topic.pro_stance
        assert item.con_stance == topic.con_stance
        assert item.pro_points == topic.pro_points
        assert item.con_points == topic.con_points


def test_load_golden_set_rejects_bad_lines(tmp_path):
    valid_line = json.dumps(
        {"id": "x01", "domain": "d", "source": "s", "title": "t",
         "pro_stance": "a", "con_stance": "b", "pro_points": ["p"], "con_points": ["c"]},
        ensure_ascii=False,
    )
    bad_json = tmp_path / "bad.jsonl"
    bad_json.write_text(valid_line + "\n这不是 JSON\n", encoding="utf-8")
    with pytest.raises(ValueError, match=":2"):
        load_golden_set(bad_json)

    dup = tmp_path / "dup.jsonl"
    dup.write_text(valid_line + "\n" + valid_line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        load_golden_set(dup)


def test_to_topic_roundtrip():
    topic = _ITEM.to_topic()
    assert topic.topic_id == "g01"
    assert topic.stance_for(Camp.PRO) == _ITEM.pro_stance
    assert topic.points_for(Camp.CON) == _ITEM.con_points


# ---- judge 解析 ----


def test_parse_valid_and_fence_tolerant():
    score = parse_judge_output(_valid_judge_json())
    assert score.degraded is False
    assert score.stance_adherence.pro.score == 8
    assert score.citation_integrity.con.reason == "无编造"
    # ```json 围栏 + 前后闲话也应解析
    fenced = f"好的，评测如下：\n```json\n{_valid_judge_json()}\n```\n以上。"
    assert parse_judge_output(fenced).argument_quality.con.score == 5


@pytest.mark.parametrize(
    ("bad", "match"),
    [
        ("完全不是 JSON", "未找到 JSON 对象"),
        ('{"stance_adherence": {"pro": {"score": 8, "reason": "r"}, "con": {"score": 7, "reason": "r"}}}', "缺少维度"),
        (
            json.dumps({
                "stance_adherence": {"pro": {"score": 15, "reason": "r"}, "con": {"score": 7, "reason": "r"}},
                "citation_integrity": {"pro": {"score": 8, "reason": "r"}, "con": {"score": 7, "reason": "r"}},
                "argument_quality": {"pro": {"score": 8, "reason": "r"}, "con": {"score": 7, "reason": "r"}},
            }),
            "越界",
        ),
        (
            json.dumps({
                "stance_adherence": {"pro": {"score": 8, "reason": "  "}, "con": {"score": 7, "reason": "r"}},
                "citation_integrity": {"pro": {"score": 8, "reason": "r"}, "con": {"score": 7, "reason": "r"}},
                "argument_quality": {"pro": {"score": 8, "reason": "r"}, "con": {"score": 7, "reason": "r"}},
            }),
            "为空",
        ),
    ],
)
def test_parse_rejects_malformed(bad, match):
    with pytest.raises(JudgeParseError, match=match):
        parse_judge_output(bad)


def test_judge_debate_retries_with_feedback_then_succeeds():
    gateway = _StubGateway(["垃圾输出", _valid_judge_json()])
    score = judge_debate(gateway, _ITEM, "【立论】pro-1：……", retries=1)
    assert score.degraded is False
    assert len(gateway.calls) == 2, "首次失败应带纠错反馈重试"
    first, second = gateway.calls
    assert len(second) > len(first), "重试消息须追加纠错反馈"
    assert "不合格式" in second[-1]["content"]


def test_judge_debate_degrades_after_retries_exhausted():
    gateway = _StubGateway(["还是垃圾"])
    score = judge_debate(gateway, _ITEM, "实录", retries=1)
    assert score.degraded is True
    assert score.note and "失败" in score.note
    assert len(gateway.calls) == 2  # 重试耗尽后降级，不再多打


# ---- 聚合数学 ----


def _flat_score(pro: int, con: int) -> JudgeScore:
    dim = DimensionScore(
        pro=SideScore(score=pro, reason="x"),
        con=SideScore(score=con, reason="x"),
    )
    return JudgeScore(stance_adherence=dim, citation_integrity=dim, argument_quality=dim)


def test_aggregate_means_and_degraded_excluded():
    agg = aggregate([
        _flat_score(8, 6),
        _flat_score(4, 4),
        JudgeScore.make_degraded("judge 挂了"),
    ])
    assert agg["graded"] == 2 and agg["degraded"] == 1
    for dim in DIMS:
        assert agg["dimensions"][dim]["pro"] == 6.0
        assert agg["dimensions"][dim]["con"] == 5.0
        assert agg["dimensions"][dim]["delta"] == 1.0
    assert agg["overall"] == {"pro": 6.0, "con": 5.0}


def test_aggregate_all_degraded_is_none_not_zero():
    agg = aggregate([JudgeScore.make_degraded("x"), JudgeScore.make_degraded("y")])
    assert agg["graded"] == 0 and agg["degraded"] == 2
    assert agg["overall"] == {"pro": None, "con": None}
    assert agg["dimensions"]["stance_adherence"]["pro"] is None


# ---- 实录渲染与 CLI ----


def test_render_transcript_contains_phase_speaker_and_notes():
    result = DebateResult(
        status="finished",
        transcript=[
            Speech(seq=1, phase=1, phase_name="立论", speaker="pro-1", camp=Camp.PRO,
                   text="定义与判准如下。", side_notes=["计时员：还剩 30 秒"]),
        ],
    )
    text = render_transcript(result)
    assert "【立论】pro-1（正方）：定义与判准如下。" in text
    assert "*（计时员：还剩 30 秒）*" in text


def test_cli_dry_run_validates_without_network(capsys):
    assert main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "golden set: 10 条" in out
    assert "dry-run OK" in out


def test_cli_topic_filter_and_unknown_id(capsys):
    assert main(["--dry-run", "--topics", "g01,g04", "--limit", "1"]) == 0
    assert "1 条" in capsys.readouterr().out
    assert main(["--dry-run", "--topics", "g99"]) == 1
    assert "未知 golden id" in capsys.readouterr().err
