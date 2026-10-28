"""端到端（引擎级）：图事件必须跟着辩论长出来，而不是开局给一张固定的图。

验证：
  - 每条发言后都有一个 graph 事件（版本号连续、节点数单调不减）
  - 事件里的两种视图都是合法 mermaid 头
  - 终态快照收敛/终止，且 result 带回完整历史（供落库与回看）
"""
from __future__ import annotations

import pytest

from debate.core.config import AppConfig, load_config
from debate.engine.agents.loader import load_topics
from debate.engine.llm.gateway import Gateway
from debate.engine.runner import run_debate
from debate.engine.schema import DebateFormat
from tests.fakes.compressor import fake_columns
from tests.fakes.llm import FakeLLM


@pytest.fixture(scope="module")
def lite_config(backend_root):
    cfg = load_config(backend_root / "config.yaml").model_dump()
    cfg["procedure"]["phases"]["free_debate"] = 1.0
    cfg["tools"]["elenchus"]["enabled"] = False
    return AppConfig.model_validate(cfg)


def _gateway(backend_root, fmt: str) -> Gateway:
    script = backend_root / "tests" / "fakes" / f"script_{fmt}.json"
    return Gateway(backend=FakeLLM.scripted(script), model_debater="fake", model_official="fake")


def _collect(assets_root, backend_root, lite_config, events: list[dict]) -> "object":
    """跑一场精简赛，把 speech 与 graph 事件按真实服务层的顺序塞进同一个列表。"""

    def on_speech(speech) -> None:  # 服务层同样把发言转成 speech 事件
        events.append({"event": "speech", "data": {"seq": speech.seq, "speaker": speech.speaker}})

    return run_debate(
        topic=load_topics(assets_root)[0], fmt=DebateFormat.LITE, config=lite_config,
        gateway=_gateway(backend_root, "lite"), compressor=fake_columns, assets_root=assets_root,
        on_speech=on_speech, on_event=events.append,
    )


def test_graph_events_grow_with_debate(assets_root, backend_root, lite_config):
    events: list[dict] = []
    result = _collect(assets_root, backend_root, lite_config, events)
    assert result.status == "finished"

    graphs = [e["data"] for e in events if e["event"] == "graph"]
    speeches = [e for e in events if e["event"] == "speech"]
    assert graphs, "引擎必须发出 graph 事件"
    # 每条发言都会带来一次图更新；环节结束/宣判还会额外更新
    assert len(graphs) >= len(speeches)

    versions = [g["version"] for g in graphs]
    assert versions == list(range(1, len(versions) + 1)), "版本号必须连续递增"

    counts = [g["state"]["node_count"] for g in graphs]
    assert counts == sorted(counts), "论点节点只增不减（图在生长）"
    # 环节开始时先出一张"还没发言"的图，每条发言再各长一个论点节点
    assert counts[0] == 0
    assert counts[-1] == len(speeches), "每条发言恰好长出一个论点节点"

    # 论证图语法头正确
    assert all(g["views"]["argument"].startswith("flowchart LR") for g in graphs)

    last = graphs[-1]
    assert last["terminated"] is True and last["converged"] is True

    # 引擎结果带回完整历史（终态落库与归档回看用）
    assert len(result.graph_snapshots) == len(graphs)


def test_graph_event_follows_its_speech(assets_root, backend_root, lite_config):
    """顺序契约：先看到这条发言，再看到包含它的新图。"""
    events: list[dict] = []
    _collect(assets_root, backend_root, lite_config, events)
    kinds = [e["event"] for e in events]
    first_speech = kinds.index("speech")
    first_graph = kinds.index("graph")
    # 环节开始时先推一张空论点图（控制图里已有环节节点），随后每条发言再长节点
    assert first_graph < first_speech
    assert events[first_graph]["data"]["state"]["node_count"] == 0
    seq = events[first_speech]["data"]["seq"]
    assert seq == 1
    # 第一条发言之后的那张图，必须包含它
    graph_after = next(e["data"] for e in events[first_speech:] if e["event"] == "graph")
    assert graph_after["state"]["node_count"] == 1
    assert graph_after["active_node"] == "n1"
