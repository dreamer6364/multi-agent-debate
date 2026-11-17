"""评测 CLI（ISS-014 A-1）：golden set 比赛 → LLM-as-judge 三维评分 → 报告落盘。

用法（backend/ 目录下）：
    python -m eval.run_eval --dry-run                       # 只验资产，不出网不耗 token
    python -m eval.run_eval --topics g01 --format lite --label smoke
    python -m eval.run_eval --limit 3 --label before-prompt  # prompt 改动前基线
    python -m eval.run_eval --limit 3 --label after-prompt   # 改动后对比（同题同参）

--label 决定报告文件名（eval/reports/<label>.json），前后两次跑分各落一份即可对比。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from debate.core.config import load_config, load_env
from debate.engine.agents.loader import PromptAssetError, load_agents
from debate.engine.llm.compressor import make_llm_columns_compressor
from debate.engine.llm.gateway import Gateway, extra_body_from_env
from debate.engine.runner import DebateResult, run_debate
from debate.engine.schema import CAMP_CN, DebateFormat

from .dataset import load_golden_set
from .judge import DIMS, DIM_CN, JudgeScore, judge_debate


def render_transcript(result: DebateResult) -> str:
    """实录转 judge 可读文本：环节 + 说话人（阵营）+ 正文，旁注独立成行。"""
    lines: list[str] = []
    for speech in result.transcript:
        lines.append(
            f"【{speech.phase_name}】{speech.speaker}（{CAMP_CN[speech.camp]}）：{speech.text}"
        )
        for note in speech.side_notes:
            lines.append(f"*（{note}）*")
    return "\n".join(lines)


def _mean(values: list[int]) -> float | None:
    return round(fmean(values), 2) if values else None


def aggregate(scores: list[JudgeScore]) -> dict[str, Any]:
    """跨题目聚合：三维 × 双方均分 + 总均分；degraded 份剔除并计数。

    全部降级时均分为 None（报告如实呈现，不拿 0 分冒充成绩）。
    """
    valid = [s for s in scores if not s.degraded]
    dimensions: dict[str, Any] = {}
    for dim in DIMS:
        pro = [getattr(s, dim).pro.score for s in valid]
        con = [getattr(s, dim).con.score for s in valid]
        pro_m, con_m = _mean(pro), _mean(con)
        dimensions[dim] = {
            "pro": pro_m,
            "con": con_m,
            "delta": round(pro_m - con_m, 2) if pro_m is not None and con_m is not None else None,
        }
    all_pro = [getattr(s, dim).pro.score for s in valid for dim in DIMS]
    all_con = [getattr(s, dim).con.score for s in valid for dim in DIMS]
    return {
        "graded": len(valid),
        "degraded": len(scores) - len(valid),
        "dimensions": dimensions,
        "overall": {"pro": _mean(all_pro), "con": _mean(all_con)},
    }


def _build_gateway(config) -> Gateway:
    """与 CLI run 同构的真实网关（ARCH D6 双模型档 + 限速 + 透传参数）。"""
    return Gateway(
        model_debater=os.environ.get("MODEL_DEBATER", ""),
        model_official=os.environ.get("MODEL_OFFICIAL", ""),
        max_retries=config.model.max_retries,
        min_interval=float(os.environ.get("MIN_CALL_INTERVAL", config.model.min_call_interval)),
        extra_body=extra_body_from_env(),
    )


def _print_dry_run(items) -> None:
    print(f"[eval] golden set: {len(items)} 条")
    for item in items:
        print(f"  - {item.id} [{item.domain}] {item.title}（{item.source}）")


def _print_aggregate(agg: dict[str, Any]) -> None:
    print("[eval] 三维均分（0~10，judge 独立评分）：")
    for dim in DIMS:
        d = agg["dimensions"][dim]
        print(f"  {DIM_CN[dim]}（{dim}）: 正方 {d['pro']} / 反方 {d['con']}（Δ {d['delta']}）")
    print(
        f"[eval] 总均分: 正方 {agg['overall']['pro']} / 反方 {agg['overall']['con']}"
        f" | 有效 {agg['graded']} 份、降级 {agg['degraded']} 份"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eval.run_eval",
        description="golden set + LLM-as-judge 三维评分（ISS-014 A-1）",
    )
    parser.add_argument("--config", default="config.yaml", help="配置文件（默认 cwd 下 config.yaml）")
    parser.add_argument("--golden", default=None, help="golden set jsonl 路径（默认 eval/golden_set.jsonl）")
    parser.add_argument("--topics", default=None, help="逗号分隔的 golden id 过滤，如 g01,g04")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条（控成本）")
    parser.add_argument("--format", default="lite", choices=["full", "lite"], help="赛制（默认 lite 省 token）")
    parser.add_argument("--label", default=None, help="报告标签（改前改后跑分对比用），默认时间戳")
    parser.add_argument("--out", default=None, help="报告目录（默认 eval/reports/）")
    parser.add_argument("--dry-run", action="store_true", help="只校验资产与过滤结果，不出网不耗 token")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        items = load_golden_set(args.golden)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[eval] FAIL: {exc}", file=sys.stderr)
        return 1

    if args.topics:
        wanted = {t.strip() for t in args.topics.split(",") if t.strip()}
        known = {item.id for item in items}
        unknown = wanted - known
        if unknown:
            print(f"[eval] FAIL: 未知 golden id: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1
        items = [item for item in items if item.id in wanted]
    if args.limit is not None and args.limit > 0:
        items = items[: args.limit]

    if args.dry_run:
        _print_dry_run(items)
        config_path = Path(args.config)
        if config_path.exists():  # 顺带验 Prompt 资产（loader fail-fast）
            load_config(config_path)
            agents = load_agents(config_path.parent)
            print(f"[eval] 配置与 Prompt 资产 OK（{len(agents)} 角色）")
        print("[eval] dry-run OK（未出网、未耗 token）")
        return 0

    config_path = Path(args.config)
    try:
        config = load_config(config_path)
    except Exception as exc:  # noqa: BLE001 —— 配置错误直接给可读失败信息
        print(f"[eval] FAIL: 配置加载失败 {config_path}: {exc}", file=sys.stderr)
        return 1
    load_env(config_path.parent / ".env")
    gateway = _build_gateway(config)
    compressor = make_llm_columns_compressor(gateway, temperature=config.model.temperature.digest)
    fmt = DebateFormat(args.format)

    report_items: list[dict[str, Any]] = []
    scores: list[JudgeScore] = []
    for idx, item in enumerate(items, 1):
        print(f"[eval] ({idx}/{len(items)}) {item.id} {item.title}")
        result = run_debate(
            topic=item.to_topic(), fmt=fmt, config=config, gateway=gateway,
            compressor=compressor, assets_root=config_path.parent,
        )
        if result.transcript:
            score = judge_debate(gateway, item, render_transcript(result))
        else:
            score = JudgeScore.make_degraded(f"比赛未产出实录（{result.status}: {result.error}）")
        scores.append(score)
        report_items.append({
            "id": item.id,
            "domain": item.domain,
            "title": item.title,
            "debate_status": result.status,
            "error": result.error,
            "winner": result.verdict.winner.value
            if result.verdict and result.verdict.winner is not None else None,
            "speech_count": len(result.transcript),
            # 实录随报告存档：失败时可直接定位到哪条发言降级，成功时报告自含证据
            "transcript": render_transcript(result),
            "judge": score.model_dump(),
        })
        judge_state = "降级" if score.degraded else "OK"
        print(f"        比赛={result.status} | judge={judge_state}")

    agg = aggregate(scores)
    label = args.label or datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else Path(__file__).parent / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "label": label,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "format": args.format,
        "golden_count": len(items),
        "items": report_items,
        "aggregate": agg,
        "llm_usage": dict(gateway.usage),
    }
    report_path = out_dir / f"{label}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    _print_aggregate(agg)
    print(f"[eval] 报告已落盘: {report_path}")
    finished = sum(1 for entry in report_items if entry["debate_status"] == "finished")
    if finished == 0 and report_items:
        return 2
    return 0


if __name__ == "__main__":
    try:
        _code = main()
    except PromptAssetError as exc:  # Prompt 资产 fail-fast → 可读失败而非 traceback
        print(f"[eval] FAIL: {exc}", file=sys.stderr)
        _code = 1
    raise SystemExit(_code)
