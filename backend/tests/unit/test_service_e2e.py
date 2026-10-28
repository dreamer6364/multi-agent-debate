"""服务层端到端（v1.9.2）：整场跑完（含评委环节）必须收敛为 finished 并落库。

用 SQLite + FakeLLM 剧本回放复现真实 `DebateService.start()` 路径：
引擎线程 → 发言入队落库 → 评审 → 宣判 → finalize → 总线发终态。
这是"评委点评环节卡住"最贴近现场的回归用例（历史缺陷：finalize 抛异常后
场次永远停在 running，前端一直显示"评委合议中"）。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from debate.core.config import AppConfig, load_config
from debate.db.models import DebateRow, ScorecardRow, SpeechRow
from debate.engine.agents.loader import load_agents, load_topics
from debate.engine.llm.gateway import Gateway
from debate.engine.schema import DebateFormat
from debate.services.debate_service import DebateService
from tests.fakes.compressor import fake_columns
from tests.fakes.llm import FakeLLM


@pytest.fixture(scope="module")
def run_config(backend_root):
    cfg = load_config(backend_root / "config.yaml").model_dump()
    # 与 tests/unit/test_graph.py 对齐：免费档剧本只够每方 2 回合自由辩
    cfg["procedure"]["phases"]["free_debate"] = 1.0
    cfg["tools"]["elenchus"]["enabled"] = False      # 不触发真实 Elenchus
    cfg["tools"]["agent"]["enabled"] = False         # 不触发工具循环（纯净链路）
    cfg["web_search"]["enabled"] = False             # 不出网
    cfg["model"]["min_call_interval"] = 0            # 不做限速等待
    cfg["judges"]["heartbeat_seconds"] = 0.2         # 快速心跳，验证进度事件
    return AppConfig.model_validate(cfg)


def test_full_debate_reaches_terminal_state(tmp_path, assets_root, backend_root, run_config, monkeypatch):
    from tests.unit.test_service_job import TOPIC

    async def main():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            session.add(DebateRow(id=1, topic_id=1, owner_id=1, format="lite", status="pending"))
            await session.commit()

        agents = load_agents(assets_root)
        topic = load_topics(assets_root)[0]
        gateway = Gateway(
            backend=FakeLLM.scripted(backend_root / "tests" / "fakes" / "script_lite.json"),
            model_debater="fake", model_official="fake", max_retries=1, sleep=lambda s: None,
        )
        svc = DebateService(
            config=run_config, agents=agents, sessionmaker=maker,
            gateway_factory=lambda cfg, fmt: gateway,
            compressor_factory=lambda cfg, gw: fake_columns,
            now_fn=lambda: __import__("datetime").datetime(2026, 10, 27, 12, 0, 0),
        )
        svc.start(debate_id=1, topic=topic, fmt=DebateFormat.LITE)
        bus = svc.get_bus(1)
        queue = bus.subscribe()
        await asyncio.wait_for(svc._tasks[1], timeout=180)

        events: list[dict] = []
        while not queue.empty():
            events.append(queue.get_nowait())

        # 1) 终态必达 + 总线关闭
        statuses = [e["data"]["status"] for e in events if e["event"] == "status"]
        assert statuses and statuses[-1] == "finished", statuses
        assert bus.closed is True
        # 2) 评审进度有播报（前端据此显示已评议时长）
        assert any(e["event"] == "judging_progress" for e in events)
        assert any(e["event"] == "judging" for e in events)
        assert any(e["event"] == "verdict" for e in events)

        # 3) 落库完整：发言含评委点评、评分卡 3 张、场次终态与胜方
        async with maker() as session:
            rows = (await session.execute(select(SpeechRow).order_by(SpeechRow.seq))).scalars().all()
            assert rows, "发言未落库"
            seqs = [r.seq for r in rows]
            assert len(seqs) == len(set(seqs)), "重复 seq 会撞 uq_debate_seq（现场 1062 报错根因）"
            assert any(r.speaker_role == "judge-logic" for r in rows)  # 评委点评已写入实录
            # v2.2：主席开篇（phase 1）与结束致辞（phase 11）必须在实录里，立论框架页才有内容
            assert any(r.speaker_role == "chair" and r.phase == 1 for r in rows)
            assert any(r.speaker_role == "chair" and r.phase == 11 for r in rows)
            cards = (await session.execute(select(ScorecardRow))).scalars().all()
            assert len(cards) == 3
            debate = await session.get(DebateRow, 1)
            assert debate.status == "finished"
            assert debate.winner in ("pro", "con")  # 不再出现平局
            assert debate.finished_at is not None

    asyncio.run(main())
