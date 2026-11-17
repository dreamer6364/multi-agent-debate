"""LLM-as-judge 三维评分（ISS-014 A-1）。

三维对齐 ISS-014 修复建议（立场一致 / 引用属实 / 逻辑谬误）：
- stance_adherence  立场坚守：是否守住本方立场，有无中途倒戈、和稀泥、承认对方整体正确；
- citation_integrity 引用诚信：引用是否出自实录与证据素材，有无编造数据与虚构引用；
- argument_quality  论证质量：论证链完整度与逻辑谬误密度（偷换概念、以偏概全、滑坡等）。

双方各 0~10 分 + 理由（judge 独立于场上三评委，评的是**输出质量**而非赛果）。

工程口径：
- judge 走 tier="judge"（廉价模型 ARCH D6）+ temperature=0.1（低随机，利于改前改后对比）；
- JSON 解析：``` 围栏剥离 → 首 { 末 } 截取 → 逐维校验（0~10 数值、理由非空）；
- 解析失败自动带纠错反馈重试，重试耗尽**降级**（degraded=True，聚合时剔除），
  单条 judge 失败绝不中断整轮评测。
"""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from debate.engine.llm.gateway import Gateway, LLMResult

from .dataset import GoldenItem

#: 三维顺序即 prompt 中的呈现顺序（聚合与报告同序）
DIMS: tuple[str, ...] = ("stance_adherence", "citation_integrity", "argument_quality")

DIM_CN: dict[str, str] = {
    "stance_adherence": "立场坚守",
    "citation_integrity": "引用诚信",
    "argument_quality": "论证质量",
}

_JUDGE_SYSTEM = (
    "你是辩论赛的中立评测裁判（LLM-as-judge），独立于场上评委团，只评估**输出质量**。"
    "你将看到一道辩题、双方立场与全场实录，请从三个维度给双方各打 0~10 分并给出理由：\n"
    "1. stance_adherence（立场坚守）：发言是否守住本方立场，有无中途倒戈、和稀泥、承认对方整体正确；\n"
    "2. citation_integrity（引用诚信）：引用是否出自实录与证据素材，有无编造精确数据、虚构来源；\n"
    "3. argument_quality（论证质量）：论证链是否完整（论点→论据→论证），逻辑谬误密度"
    "（偷换概念、以偏概全、滑坡、诉诸情感等）如何——谬误少且链条完整得分高。\n"
    "打分要拉开区分度：表现确凿可给 9~10，硬伤明显应给 0~4；每条理由必须具体、可回溯到实录内容。"
    "只输出一个 JSON 对象，不要输出任何其他文字，格式：\n"
    '{"stance_adherence": {"pro": {"score": 0, "reason": ""}, "con": {"score": 0, "reason": ""}}, '
    '"citation_integrity": {"pro": {"score": 0, "reason": ""}, "con": {"score": 0, "reason": ""}}, '
    '"argument_quality": {"pro": {"score": 0, "reason": ""}, "con": {"score": 0, "reason": ""}}}'
)


class JudgeParseError(ValueError):
    """judge 输出不合格式（JSON 缺失/维度缺失/分数越界/理由为空）。"""


class SideScore(BaseModel):
    score: int  # 0~10
    reason: str


class DimensionScore(BaseModel):
    pro: SideScore
    con: SideScore


class JudgeScore(BaseModel):
    """一份三维评分结果；degraded=True 表示解析降级（聚合时剔除）。"""

    stance_adherence: DimensionScore
    citation_integrity: DimensionScore
    argument_quality: DimensionScore
    degraded: bool = False
    note: str | None = None

    @classmethod
    def make_degraded(cls, note: str) -> JudgeScore:
        zero = DimensionScore(
            pro=SideScore(score=0, reason=note),
            con=SideScore(score=0, reason=note),
        )
        return cls(
            stance_adherence=zero, citation_integrity=zero, argument_quality=zero,
            degraded=True, note=note,
        )


_FENCE = re.compile(r"```[a-zA-Z]*")


def build_judge_prompt(item: GoldenItem, transcript: str) -> list[dict[str, str]]:
    """judge 调用消息：system（角色+三维口径+JSON schema）+ user（辩题基准+实录）。"""
    user = (
        f"【辩题】{item.title}\n"
        f"【正方立场】{item.pro_stance}\n"
        f"【反方立场】{item.con_stance}\n"
        f"【全场实录】\n{transcript}\n\n"
        "请按 system 规定的 JSON 格式给双方三个维度打分。"
    )
    return [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]


def _parse_side(obj: dict[str, Any], side: str, dim: str) -> SideScore:
    item = obj.get(side)
    if not isinstance(item, dict):
        raise JudgeParseError(f"缺少 {dim}.{side}")
    raw_score = item.get("score")
    if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
        raise JudgeParseError(f"{dim}.{side}.score 非法: {raw_score!r}")
    if not (0 <= raw_score <= 10):
        raise JudgeParseError(f"{dim}.{side}.score 越界（0~10）: {raw_score!r}")
    reason = item.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise JudgeParseError(f"{dim}.{side}.reason 缺失或为空")
    return SideScore(score=int(round(raw_score)), reason=reason.strip())


def parse_judge_output(raw: str) -> JudgeScore:
    """解析 judge 输出（``` 围栏与前后闲话容错，逐维严格校验）。"""
    text = _FENCE.sub("", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise JudgeParseError("未找到 JSON 对象")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise JudgeParseError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise JudgeParseError("JSON 顶层须为对象")

    dims: dict[str, DimensionScore] = {}
    for dim in DIMS:
        obj = data.get(dim)
        if not isinstance(obj, dict):
            raise JudgeParseError(f"缺少维度 {dim}")
        dims[dim] = DimensionScore(
            pro=_parse_side(obj, "pro", dim),
            con=_parse_side(obj, "con", dim),
        )
    return JudgeScore(**dims)


def judge_debate(
    gateway: Gateway,
    item: GoldenItem,
    transcript: str,
    *,
    retries: int = 1,
    max_tokens: int = 1800,
) -> JudgeScore:
    """评一场：调用 judge 档模型 → 解析；失败带纠错反馈重试，耗尽则降级返回。

    降级而非抛异常：单条 judge 失败不应中断整轮评测（报告如实记 degraded）。
    """
    messages = build_judge_prompt(item, transcript)
    last_err: Exception | None = None
    last_raw = ""
    for _attempt in range(retries + 1):
        try:
            result: LLMResult = gateway.complete(
                messages, tier="judge", temperature=0.1, max_tokens=max_tokens,
            )
            last_raw = result.text
            return parse_judge_output(result.text)
        except JudgeParseError as exc:
            last_err = exc
            # 带纠错反馈重试：把坏输出与错误原因回喂，要求只输出合规 JSON
            messages = [
                *messages,
                {"role": "assistant", "content": last_raw[:1200]},
                {"role": "user", "content": f"上一次输出不合格式：{exc}。请重新只输出合规的 JSON 对象。"},
            ]
        except Exception as exc:  # noqa: BLE001 —— 传输层失败（LLMExhausted 等）同样降级，不中断评测
            last_err = exc
            break
    return JudgeScore.make_degraded(f"judge 失败（已重试 {retries} 次）: {last_err}")
