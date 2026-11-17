"""黄金辩题集（golden set）：评测输入资产（ISS-014 A-1）。

golden_set.jsonl 每行一个 JSON 对象（文档即代码范式，与 roles/topics 同构）：
3 条与内置 topics/*.md 完全对齐（source=builtin），7 条新领域题目（source=eval:v1.1）
覆盖科技/法律/哲理/教育/职场/医疗/环境/生活/文化，防过拟合内置三题。

加载即校验：字段齐全、id 唯一——坏行带行号 fail-fast，不带病评测。
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from debate.engine.schema import Topic

#: 默认资产位置（eval/golden_set.jsonl）
DEFAULT_GOLDEN = Path(__file__).parent / "golden_set.jsonl"


class GoldenItem(BaseModel):
    """一条黄金辩题：既是要评测的输入，也是 judge 评分时的立场基准。"""

    id: str
    domain: str
    source: str
    title: str
    pro_stance: str
    con_stance: str
    pro_points: list[str] = Field(min_length=1)
    con_points: list[str] = Field(min_length=1)

    def to_topic(self) -> Topic:
        """转引擎 Topic——与内置 topics/*.md 同构，直接喂 run_debate。"""
        return Topic(
            topic_id=self.id,
            title=self.title,
            pro_stance=self.pro_stance,
            con_stance=self.con_stance,
            pro_points=list(self.pro_points),
            con_points=list(self.con_points),
        )


def load_golden_set(path: Path | str | None = None) -> list[GoldenItem]:
    """读取 jsonl 并校验（id 唯一、逐行 pydantic 校验）。"""
    src = Path(path) if path else DEFAULT_GOLDEN
    if not src.exists():
        raise FileNotFoundError(f"golden set 不存在: {src}")
    items: list[GoldenItem] = []
    seen: set[str] = set()
    for lineno, line in enumerate(src.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{src}:{lineno} JSON 解析失败: {exc}") from exc
        try:
            item = GoldenItem.model_validate(data)
        except Exception as exc:  # noqa: BLE001 —— pydantic 异常统一转带行号的失败信息
            raise ValueError(f"{src}:{lineno} 字段校验失败: {exc}") from exc
        if item.id in seen:
            raise ValueError(f"{src}:{lineno} id 重复: {item.id}")
        seen.add(item.id)
        items.append(item)
    if not items:
        raise ValueError(f"golden set 为空: {src}")
    return items
