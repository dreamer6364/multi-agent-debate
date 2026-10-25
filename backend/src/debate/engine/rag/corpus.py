"""RAG 证据库（v1.4）：knowledge/*.md 证据卡解析（文档即代码，与 roles/topics 同范式）。

证据卡格式（文件内多张，以一级标题分隔）：
  # 证据卡：标题
  - 来源：《书名》（作者） / 文献名
  - 类型：书籍 / 文献 / 报告
  正文：观点概述（供辩手引用，引用时标注来源名）
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class EvidenceCard(BaseModel):
    card_id: str
    title: str
    source: str
    source_type: str
    text: str


class CorpusError(RuntimeError):
    """证据库缺失或格式非法（fail-fast，与 PromptAssetError 同策略）。"""


def parse_corpus(content: str, origin: str = "knowledge") -> list[EvidenceCard]:
    cards: list[EvidenceCard] = []
    blocks = content.split("\n# ")
    for index, block in enumerate(blocks):
        lines = [line for line in block.strip().splitlines() if line.strip()]
        if not lines:
            continue
        first = lines[0].lstrip("# ").strip()
        if not first.startswith("证据卡："):
            continue  # 允许文件头部自由说明文字
        title = first[len("证据卡："):].strip()
        source, source_type = "", ""
        body_start = len(lines)
        for i, line in enumerate(lines[1:], start=1):
            if line.startswith("- 来源："):
                source = line[len("- 来源："):].strip()
            elif line.startswith("- 类型："):
                source_type = line[len("- 类型："):].strip()
            else:
                body_start = i
                break
        text = "\n".join(lines[body_start:]).strip()
        if not (title and source and text):
            raise CorpusError(f"{origin}: 证据卡字段不完整（标题/来源/正文）→ {title or lines[0][:20]}")
        cards.append(EvidenceCard(
            card_id=f"ev-{index}", title=title, source=source,
            source_type=source_type or "书籍", text=text,
        ))
    return cards


def load_corpus(knowledge_dir: Path) -> list[EvidenceCard]:
    directory = Path(knowledge_dir)
    if not directory.exists():
        raise CorpusError(f"证据库目录不存在: {directory}")
    cards: list[EvidenceCard] = []
    for path in sorted(directory.glob("*.md")):
        cards.extend(parse_corpus(path.read_text(encoding="utf-8"), origin=str(path)))
    if not cards:
        raise CorpusError(f"证据库为空: {directory}")
    return cards
