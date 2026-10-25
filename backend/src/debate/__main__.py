"""CLI 入口（ARCH A6）：check 自检 + run 直跑一场比赛。

run 的 --fake 为开发档：以 tests/fakes 剧本回放，不出网、不耗 token。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .core.config import AppConfig, deep_merge, load_config, load_env
from .engine.agents.loader import PromptAssetError, load_agents, load_topics
from .engine.render.markdown import render_scorecard_md, render_transcript_md, role_display
from .engine.schema import DebateFormat
from .engine.sim.timer import words_for


def cmd_check(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = load_config(config_path)
    load_env(config_path.parent / ".env")

    agents = load_agents(config_path.parent)
    topics = load_topics(config_path.parent)

    print(f"[check] 配置加载成功: {config_path}")
    print(f"[check] 语速 {config.procedure.speed_cpm} 字/分钟 | 评委 {config.judges.count} 人")
    phases = config.procedure.phases
    for name in type(phases).model_fields:
        minutes = getattr(phases, name)
        print(f"  - {name:<20} {minutes:>5} 分钟 ≈ {words_for(minutes, config.procedure.speed_cpm):>4} 字")
    llm_count = sum(1 for agent in agents.values() if agent.llm)
    print(f"[check] Prompt 角色: {len(agents)} 个（LLM 驱动 {llm_count} 个，代码扮演 {len(agents) - llm_count} 个）")
    print(f"[check] 内置辩题: {len(topics)} 个")
    for topic in topics:
        print(f"  - {topic.topic_id}: {topic.title}")
    print("[check] OK")
    return 0


def _fake_compressor(speeches):
    """开发档摘要压缩：确定性五栏（不调模型）。"""
    if not speeches:
        return {}
    return {
        "definition_judge": "双方定义与判准已记录在案。",
        "arguments": "双方论点继续展开并互相拆解。",
        "clashes": "本环节双方围绕判准与定义直接交锋。",
        "fallacies": "双方互相指出对方论证瑕疵。",
        "situation": "局势推进，战场逐渐聚焦。",
    }


def cmd_run(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = load_config(config_path)
    load_env(config_path.parent / ".env")
    root = config_path.parent

    topics = load_topics(root)
    topic = next((t for t in topics if t.topic_id == args.topic), None)
    if topic is None:
        print(f"[run] 未找到辩题: {args.topic}（可选：{', '.join(t.topic_id for t in topics)}）", file=sys.stderr)
        return 1
    fmt = DebateFormat(args.format)

    if args.fake:
        sys.path.insert(0, str(root.parent))
        from tests.fakes.llm import FakeLLM

        script_path = root.parent / "tests" / "fakes" / f"script_{fmt.value}.json"
        if not script_path.exists():
            print(f"[run] 开发档剧本不存在: {script_path}", file=sys.stderr)
            return 1
        import json

        script_meta = json.loads(script_path.read_text(encoding="utf-8")).get("meta", {})
        overrides = script_meta.get("config_overrides", {})
        if overrides:  # 剧本自带配置覆盖（如自由辩时长），保证轮次对齐
            config = AppConfig.model_validate(deep_merge(config.model_dump(), overrides))
        from .engine.llm.gateway import Gateway, extra_body_from_env

        gateway = Gateway(backend=FakeLLM.scripted(script_path), model_debater="fake", model_official="fake")
        compressor = _fake_compressor
    else:
        from .engine.llm.compressor import make_llm_columns_compressor
        from .engine.llm.gateway import Gateway, extra_body_from_env
        import os

        gateway = Gateway(
            model_debater=os.environ.get("MODEL_DEBATER", ""),
            model_official=os.environ.get("MODEL_OFFICIAL", ""),
            max_retries=config.model.max_retries,
            min_interval=config.model.min_call_interval,
            extra_body=extra_body_from_env(),
        )
        compressor = make_llm_columns_compressor(gateway, temperature=config.model.temperature.digest)

    from .engine.runner import run_debate

    def on_speech(speech):
        print(f"[{speech.phase_name}] {role_display(speech.speaker)}：{speech.text[:48]}{'…' if len(speech.text) > 48 else ''}")

    result = run_debate(
        topic=topic, fmt=fmt, config=config, gateway=gateway, compressor=compressor,
        assets_root=root, on_speech=on_speech,
    )
    print(f"\n[run] 比赛结束：{result.status}" + (f"（{result.error}）" if result.error else ""))

    out_dir = root / config.export.output_dir / datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "transcript.md").write_text(render_transcript_md(topic, fmt, result, config), encoding="utf-8")
    (out_dir / "scorecard.md").write_text(render_scorecard_md(topic, fmt, result), encoding="utf-8")
    print(f"[run] 实录与评分表已写入: {out_dir}")
    if result.verdict:
        from .engine.schema import CAMP_CN

        winner = CAMP_CN.get(result.verdict.winner, "未分胜负") if result.verdict.winner else "未分胜负"
        print(f"[run] 胜方：{winner} | 最佳辩手：{result.verdict.best_speaker or '无'}")
    return 0 if result.status == "finished" else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="debate", description="多 Agent 对抗辩论模拟平台")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="配置与 Prompt 资产自检")
    check.add_argument("--config", default="config.yaml")
    check.set_defaults(func=cmd_check)

    run = subparsers.add_parser("run", help="直跑一场比赛（CLI 模式，CONSTRAINTS B9）")
    run.add_argument("--topic", required=True, help="辩题 id，如 topic-01")
    run.add_argument("--format", default="full", choices=["full", "lite"])
    run.add_argument("--fake", action="store_true", help="开发档：回放剧本，不调真实模型")
    run.add_argument("--config", default="config.yaml")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PromptAssetError as exc:
        print(f"[debate] FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
