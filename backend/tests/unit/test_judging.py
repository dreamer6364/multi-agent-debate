"""评委评分环节防卡死（v1.9.1）：真并发、单评委硬超时、完成即播报。"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

from debate.engine.agents.loader import AgentSpec
from debate.engine.nodes.judges import make_judging_node
from debate.engine.schema import Camp, DebateFormat, Topic
from debate.engine.sim.digest import Digest


def _score_json(vote: str, best: str) -> str:
    pro = {"statement": 20, "rebuttal_cross": 15, "attack": 10, "free": 14, "closing": 11, "conduct": 4}
    con = {k: v - 1 for k, v in pro.items()}
    return json.dumps({
        "pro_scores": pro, "con_scores": con,
        "vote": vote, "best_speaker": best,
        "highlights": {"pro": ["“我方引用三年追踪数据”", "“判准回到用户福祉”"],
                        "con": ["“对方样本只覆盖城市”", "“依赖不等于失控”"]},
        "weaknesses": {"pro": ["“未回应样本偏差质疑”", "“定义前后不一”"],
                        "con": ["“归谬推演越界”", "“回避了效率代价”"]},
    }, ensure_ascii=False)


class _SlowBackend:
    """记录并发峰值并按固定延迟返回合法评分卡的替身。"""

    def __init__(self, delay: float, votes: list[str]):
        self.delay = delay
        self.votes = list(votes)
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_concurrent = 0

    def complete(self, messages, model=None, temperature=None, max_tokens=None, **kw):
        with self._lock:
            self._in_flight += 1
            self.max_concurrent = max(self.max_concurrent, self._in_flight)
        time.sleep(self.delay)
        with self._lock:
            self._in_flight -= 1
        vote = self.votes.pop(0) if self.votes else "pro"
        return SimpleNamespace(text=_score_json(vote, "pro-1"),
                               prompt_tokens=1, completion_tokens=1)


def _ctx(backend, timeout: float):
    judges = ["judge-logic", "judge-content", "judge-expression"]
    agents = {
        j: AgentSpec(role_id=j, camp=Camp.NEUTRAL, position_cn="评委", system_prompt="侧重")
        for j in judges
    }
    topic = Topic(topic_id="t1", title="A / B", pro_stance="支持 A", con_stance="支持 B",
                  pro_points=["要点一"], con_points=["要点二"])
    return SimpleNamespace(
        gateway=SimpleNamespace(complete=backend.complete),
        agents=agents, topic=topic, format=DebateFormat.FULL,
        config=SimpleNamespace(
            judges=SimpleNamespace(count=3, timeout_seconds=timeout),
            model=SimpleNamespace(temperature=SimpleNamespace(judge=0.3)),
            tools=SimpleNamespace(elenchus=SimpleNamespace(enabled=False)),
        ),
        speeches=[],
        on_speech=None,
        emit=lambda *a, **k: None,
        check_cancel=lambda: None,
        record_degradation=lambda: None,
    )


def test_judging_runs_judges_concurrently():
    """三位评委并行评审：并发峰值 ≥ 2（原先同步阻塞实为串行，峰值恒为 1）。"""
    backend = _SlowBackend(delay=0.25, votes=["pro", "con", "pro"])
    ctx = _ctx(backend, timeout=30.0)
    node = make_judging_node(ctx)
    result = node({"transcript": [], "digest": Digest(), "scorecards": None})
    assert backend.max_concurrent >= 2
    assert len(result["scorecards"]) == 3
    assert all(not c.abstained for c in result["scorecards"])
    # 完成即播报：3 条评委总结发言都已产出
    assert len(result["transcript"]) == 3


def test_judging_timeout_degrades_to_abstain_without_hanging():
    """单评委超时：按规则弃权、节点不挂起，其余评委结果不受影响。"""
    backend = _SlowBackend(delay=2.0, votes=["pro", "con", "pro"])
    ctx = _ctx(backend, timeout=0.3)
    node = make_judging_node(ctx)
    start = time.monotonic()
    result = node({"transcript": [], "digest": Digest(), "scorecards": None})
    elapsed = time.monotonic() - start
    assert elapsed < 1.5  # 未等待 2s 的慢调用结束
    assert len(result["scorecards"]) == 3
    assert all(c.abstained for c in result["scorecards"])
    # 超时的评委发言带旁注说明
    notes = [s.side_notes for s in result["transcript"]]
    assert all(any("评审超时" in x for x in n) for n in notes), f"notes={notes!r}"


# ---- v1.9.2：评审期进度心跳 + 质检彻底隔离（用户报的"评委点评环节依旧卡住"回归） ----

def _ctx_with_events(backend, *, timeout=30.0, heartbeat=10.0, elenchus_enabled=True):
    ctx = _ctx(backend, timeout=timeout)
    events: list[dict] = []
    ctx.emit = lambda _kind, **data: events.append({"event": _kind, **data})
    ctx.config.judges = SimpleNamespace(
        count=3, timeout_seconds=timeout, heartbeat_seconds=heartbeat,
    )
    ctx.config.tools = SimpleNamespace(elenchus=SimpleNamespace(
        enabled=elenchus_enabled, per_camp=1, check="fallacy", timeout_seconds=5.0,
        mode="inproc", total_budget_seconds=0.2,
    ))
    return ctx, events


def test_judging_emits_progress_heartbeat():
    """评审期必须持续推 judging_progress（阶段 + 完成数 + 耗时），前端才不会只看到静止文案。"""
    backend = _SlowBackend(delay=0.6, votes=["pro", "con", "pro"])
    ctx, events = _ctx_with_events(backend, heartbeat=0.1)
    node = make_judging_node(ctx)
    node({"transcript": [], "digest": Digest(), "scorecards": None})

    progress = [e for e in events if e["event"] == "judging_progress"]
    assert len(progress) >= 3, progress          # 心跳至少推了多轮
    assert all("elapsed" in p and p["total"] == 3 for p in progress)
    stages = [p["stage"] for p in progress]
    assert "质检" in stages
    assert any(s.startswith("评审") for s in stages)
    assert max(p["completed"] for p in progress) == 3


def test_elenchus_hang_cannot_block_judging(monkeypatch):
    """质检彻底挂死（模拟 elenchus 内部 LLM 假死）：评委阶段照常推进，绝不卡住。"""
    from debate.engine.nodes import judges as judges_module
    from debate.engine.tools import elenchus as elenchus_module

    monkeypatch.setattr(judges_module, "ELENCHUS_GUARD_MARGIN", 0.5)

    def hang(**_kwargs):
        time.sleep(30)  # 内部完全不返回
        return "", []

    monkeypatch.setattr(elenchus_module, "analyze_speeches_detailed", hang)

    backend = _SlowBackend(delay=0.05, votes=["pro", "con", "pro"])
    ctx, _events = _ctx_with_events(backend)
    node = make_judging_node(ctx)
    start = time.monotonic()
    result = node({"transcript": [], "digest": Digest(), "scorecards": None})
    elapsed = time.monotonic() - start

    assert elapsed < 3.0, f"质检挂死拖住了评委阶段：{elapsed:.1f}s"
    assert len(result["scorecards"]) == 3
    assert all(not c.abstained for c in result["scorecards"])
    assert result["machine_marks"] == []


# ---- v2.4：质检与评审并行 + 质检内部分析并行 + 评审期限速覆盖 ----

def _speech(seq: int, camp: Camp, speaker: str, text: str):
    from debate.engine.schema import Speech
    return Speech(seq=seq, phase=2, phase_name="开篇立论", speaker=speaker,
                  camp=camp, text=text, word_count=len(text))


def test_qc_runs_parallel_with_judges(monkeypatch):
    """质检不再阻塞在评审之前：旧路径 = 质检 + 评审，新路径 ≈ max(质检, 评审)。"""
    from debate.engine.tools import elenchus as elenchus_module

    def slow_qc(*_args, **_kwargs):
        time.sleep(1.2)
        return ("【机器论证分析】\n- [正方·pro-1] 稻草人（高）：引用“对方样本只覆盖城市”",
                [{"seq": 1, "camp": "pro", "speaker": "pro-1", "name": "稻草人",
                  "quote": "对方样本只覆盖城市", "explanation": "曲解对方判准", "confidence": "高"}])

    monkeypatch.setattr(elenchus_module, "analyze_speeches_detailed", slow_qc)

    backend = _SlowBackend(delay=1.0, votes=["pro", "con", "pro"])
    ctx, events = _ctx_with_events(backend, timeout=30.0)
    # total_budget=0.2 → 逻辑评委只等 0.2s 就先评审（QC_JOIN_WAIT=min(budget,20)）；
    # 质检 1.2s 由 _run_bounded 的预算+余量兜住（0.2+10s），照常完成并收割标注
    ctx.config.tools.elenchus = SimpleNamespace(
        enabled=True, per_camp=1, check="fallacy", timeout_seconds=5.0,
        mode="inproc", total_budget_seconds=0.2, parallel=2,
    )
    node = make_judging_node(ctx)
    start = time.monotonic()
    result = node({"transcript": [], "digest": Digest(), "scorecards": None})
    elapsed = time.monotonic() - start

    # 旧串行路径 ≈ 1.2(质检) + 1.0(评审) = 2.2s；并行后两位评委与质检同时跑 ≈ 1.5s
    assert elapsed < 1.9, f"质检未与评审并行：{elapsed:.2f}s"
    assert result["machine_marks"] and result["machine_marks"][0]["name"] == "稻草人"
    assert any(e["event"] == "fallacy" for e in events)  # 谬误标注照常播报
    assert all(not c.abstained for c in result["scorecards"])


def test_elenchus_items_analyzed_in_parallel(monkeypatch):
    """质检内部分析并行：4 条核心论点按 parallel=2 两批跑，墙钟 ≈ 一半。"""
    from debate.engine.tools import elenchus as elenchus_module

    lock = threading.Lock()
    inflight = {"cur": 0, "max": 0}

    def fake_analyze_one(text, check="fallacy", timeout=45.0, mode="inproc", client=None):
        with lock:
            inflight["cur"] += 1
            inflight["max"] = max(inflight["max"], inflight["cur"])
        time.sleep(0.5)
        with lock:
            inflight["cur"] -= 1
        return {"findings": [{"name": "循环论证", "quote": text[:10],
                              "confidence": "高", "explanation": "用主张自证主张"}]}

    monkeypatch.setattr(elenchus_module, "analyze_one", fake_analyze_one)
    long_text = "这是一段足够长的核心论证文本，用来撑起立论主体的篇幅与质量。" * 4
    speeches = [
        _speech(1, Camp.PRO, "pro-1", long_text), _speech(3, Camp.PRO, "pro-2", long_text),
        _speech(2, Camp.CON, "con-1", long_text), _speech(4, Camp.CON, "con-2", long_text),
    ]
    start = time.monotonic()
    text, marks = elenchus_module.analyze_speeches_detailed(
        speeches, per_camp=2, timeout=5.0, total_budget=10.0, parallel=2)
    elapsed = time.monotonic() - start

    assert inflight["max"] >= 2                      # 真并行，而非顺序排队
    assert elapsed < 1.6, f"质检未并行：{elapsed:.2f}s（顺序应为 2.0s）"
    assert len(marks) == 4 and "循环论证" in text


def test_judge_throttle_override_and_restore():
    """评审期临时收紧限速（15s → 0.2s），节点结束后恢复全局值；3 位评委不再排队 45s。"""
    from debate.engine.llm.gateway import Gateway

    backend = _SlowBackend(delay=0.05, votes=["pro", "con", "pro"])
    gateway = Gateway(backend=backend, model_debater="fake", model_official="fake",
                      min_interval=15.0, sleep=time.sleep)
    ctx = _ctx(backend, timeout=30.0)
    ctx.gateway = gateway
    ctx.config.judges = SimpleNamespace(
        count=3, timeout_seconds=30.0, heartbeat_seconds=10.0, throttle_seconds=0.2,
    )
    node = make_judging_node(ctx)
    start = time.monotonic()
    node({"transcript": [], "digest": Digest(), "scorecards": None})
    elapsed = time.monotonic() - start

    assert gateway.min_interval == 15.0              # 离开评审期必须恢复全局间隔
    assert elapsed < 3.0, f"评审期限速未生效：{elapsed:.2f}s（旧值 15s×3 ≈ 45s 排队）"


def test_judge_speed_config_defaults():
    """v2.6 提速旋钮默认值：评审限速关闭（完全并行）、质检等待收紧、质检并行度 4。"""
    from debate.core.config import AppConfig

    cfg = AppConfig()
    assert cfg.judges.throttle_seconds == 0.0
    assert cfg.judges.qc_join_wait_seconds <= 12.0
    assert cfg.judges.qc_final_wait_seconds <= 12.0
    assert cfg.tools.elenchus.parallel >= 4


def test_qc_join_wait_comes_from_config(monkeypatch):
    """v2.6：逻辑评委等质检的上限读 judges.qc_join_wait_seconds，不再硬编码 20s。

    质检挂 5s、评委 0.05s 即回：join_wait=0.3 → 逻辑评委 0.3s 后先评审，
    收尾补收割（qc_final_wait_seconds 默认 10）在质检完成时立即返回。全程 < 3s。
    （旧硬编码 20s 时同样 <3s——本用例锁的是"读配置"这条路径本身。）
    """
    from debate.engine.tools import elenchus as elenchus_module

    def slow_qc(*_args, **_kwargs):
        time.sleep(1.0)
        return "", []

    monkeypatch.setattr(elenchus_module, "analyze_speeches_detailed", slow_qc)

    backend = _SlowBackend(delay=0.05, votes=["pro", "con", "pro"])
    ctx, _events = _ctx_with_events(backend, timeout=30.0)
    ctx.config.judges = SimpleNamespace(
        count=3, timeout_seconds=30.0, heartbeat_seconds=10.0, throttle_seconds=0.0,
        qc_join_wait_seconds=0.3, qc_final_wait_seconds=0.5,
    )
    ctx.config.tools.elenchus = SimpleNamespace(
        enabled=True, per_camp=1, check="fallacy", timeout_seconds=5.0,
        mode="inproc", total_budget_seconds=5.0, parallel=2,
    )
    node = make_judging_node(ctx)
    start = time.monotonic()
    result = node({"transcript": [], "digest": Digest(), "scorecards": None})
    elapsed = time.monotonic() - start

    assert len(result["scorecards"]) == 3
    # 质检 1.0s 完成，收尾收割上限 0.5s：总耗时被两个配置值夹住，绝不回到 20+30s
    assert elapsed < 3.0, f"质检等待未按配置收紧：{elapsed:.2f}s"
