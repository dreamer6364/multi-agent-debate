"""图层单测（TEST_PLAN UT-GRF-01~03 + FT-LLM-02，SPEC §4.1/§4.9）。"""
import pytest

from debate.core.config import AppConfig, load_config
from debate.engine.agents.loader import load_topics
from debate.engine.llm.gateway import Gateway
from debate.engine.runner import run_debate
from debate.engine.schema import Camp, DebateFormat
from tests.fakes.compressor import fake_columns
from tests.fakes.llm import FakeLLM


@pytest.fixture(scope="module")
def lite_config(backend_root):
    cfg = load_config(backend_root / "config.yaml").model_dump()
    cfg["procedure"]["phases"]["free_debate"] = 1.0  # 池 220/方，短打上限 110 → 每方 2 回合
    return AppConfig.model_validate(cfg)


def _gateway(backend_root, fmt: str) -> Gateway:
    script = backend_root / "tests" / "fakes" / f"script_{fmt}.json"
    return Gateway(backend=FakeLLM.scripted(script), model_debater="fake", model_official="fake")


def test_lite_full_run(assets_root, backend_root, lite_config):
    result = run_debate(
        topic=load_topics(assets_root)[0], fmt=DebateFormat.LITE, config=lite_config,
        gateway=_gateway(backend_root, "lite"), compressor=fake_columns, assets_root=assets_root,
    )
    assert result.status == "finished"
    phases = sorted({s.phase for s in result.transcript})
    assert phases == [2, 4, 7, 8, 9, 10]  # lite 仅五环节 + 宣判（UT-GRF-02）
    assert [s.speaker for s in result.transcript if s.phase == 2] == ["pro-1", "con-1"]
    assert [s.speaker for s in result.transcript if s.phase == 4] == ["con-2", "pro-2"]
    assert [s.speaker for s in result.transcript if s.phase == 7] == ["pro-1", "con-1", "pro-2", "con-2"]
    assert [s.speaker for s in result.transcript if s.phase == 8] == ["con-4", "pro-4"]
    # 自由辩双池均耗尽（时间用尽旁注出现，UT-TIMER 联动）
    assert any("时间用尽" in n for s in result.transcript if s.phase == 7 for n in s.side_notes)
    assert len(result.scorecards) == 3 and all(not c.abstained for c in result.scorecards)
    assert result.verdict.winner is Camp.PRO and result.verdict.decisive_rule == "majority"
    assert result.verdict.votes[Camp.PRO] == 2 and result.verdict.votes[Camp.CON] == 1
    assert any(s.speaker == "chair" and s.phase == 10 for s in result.transcript)  # 宣判模板
    assert result.usage["calls"] > 0


def test_full_run_sequence_and_interrupt(assets_root, backend_root, lite_config):
    result = run_debate(
        topic=load_topics(assets_root)[0], fmt=DebateFormat.FULL, config=lite_config,
        gateway=_gateway(backend_root, "full"), compressor=fake_columns, assets_root=assets_root,
    )
    assert result.status == "finished"
    assert sorted({s.phase for s in result.transcript}) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]  # UT-GRF-01
    # 质询组发言序列：问-答-问-答 ×2 组
    cross = [s.speaker for s in result.transcript if s.phase == 3]
    assert cross == ["con-2", "pro-1", "con-2", "pro-1", "pro-2", "con-1", "pro-2", "con-1"]
    # [打断] 钩子生效：第一组一辩的首次回答被打断（SPEC §4.3 模拟口径）
    interrupted = [s for s in result.transcript if s.interrupted]
    assert interrupted and interrupted[0].speaker == "pro-1"
    assert any(n.startswith("质询方打断：") for n in interrupted[0].side_notes)
    # 攻辩：两组各 3 问 3 答
    attack = [s.speaker for s in result.transcript if s.phase == 5]
    assert attack == [
        "pro-3", "con-1", "pro-3", "con-2", "pro-3", "con-4",
        "con-3", "pro-1", "con-3", "pro-2", "con-3", "pro-4",
    ]
    assert len(result.scorecards) == 3
    assert result.verdict.winner is Camp.PRO


def test_cancel_before_run_yields_stopped(assets_root, backend_root, lite_config):
    result = run_debate(
        topic=load_topics(assets_root)[0], fmt=DebateFormat.LITE, config=lite_config,
        gateway=_gateway(backend_root, "lite"), compressor=fake_columns,
        assets_root=assets_root, cancel_check=lambda: True,
    )
    assert result.status == "stopped"  # UT-GRF-03：节点边界退出，无半条发言
    assert result.transcript == []


def test_consecutive_degradations_abort(assets_root, lite_config):
    gateway = Gateway(backend=FakeLLM(mode="fault", error_kind="http_500"),
                      model_debater="d", model_official="o", max_retries=1, sleep=lambda s: None)
    result = run_debate(
        topic=load_topics(assets_root)[0], fmt=DebateFormat.LITE, config=lite_config,
        gateway=gateway, compressor=fake_columns, assets_root=assets_root,
    )
    assert result.status == "failed"  # FT-LLM-02：连续 3 次降级熔断
    placeholders = [s for s in result.transcript if s.text == "[系统] 该辩手超时未发言"]
    assert len(placeholders) == 4  # v1.4.2：熔断阈值 5，前 4 次落占位（带占位跑完优于中止）
    assert result.error and "熔断" in result.error
