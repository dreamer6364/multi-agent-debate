"""Prompt 资产加载（SPEC §7 / AGENT_CONSTRAINTS §8）。

roles/*.md 与 rules/conduct.md 既是人读的规格也是运行时 Prompt 源（ARCH：文档即代码）。
校验失败一律 fail-fast（PromptAssetError），禁止带病启动。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from pydantic import BaseModel

from ..schema import Camp, Topic

#: 角色文件五段模板（SPEC §7.2），缺段即 fail-fast
SECTIONS = ("身份", "职责", "行为规则", "语言风格", "禁止事项")


class PromptAssetError(RuntimeError):
    """Prompt 资产缺失或校验失败。"""


class RoleDef(BaseModel):
    role_id: str
    position_cn: str
    camp: Camp
    llm: bool  # 计时员等非 LLM 角色为 False（AGENT_CONSTRAINTS §2）
    keywords: tuple[str, ...]  # 义务关键词校验（AGENT_CONSTRAINTS §8.4）


def _def(role_id: str, position_cn: str, camp: Camp, llm: bool, *keywords: str) -> None:
    ROLE_DEFS[role_id] = RoleDef(role_id=role_id, position_cn=position_cn, camp=camp, llm=llm, keywords=keywords)


ROLE_DEFS: dict[str, RoleDef] = {}
_def("pro-1", "正方一辩 · 立论手", Camp.PRO, True, "判准")
_def("pro-2", "正方二辩 · 驳论手", Camp.PRO, True, "打断")
_def("pro-3", "正方三辩 · 攻辩手", Camp.PRO, True, "盘问")
_def("pro-4", "正方四辩 · 总结手", Camp.PRO, True, "复盘")
_def("con-1", "反方一辩 · 立论手", Camp.CON, True, "判准")
_def("con-2", "反方二辩 · 驳论手", Camp.CON, True, "打断")
_def("con-3", "反方三辩 · 攻辩手", Camp.CON, True, "盘问")
_def("con-4", "反方四辩 · 总结手", Camp.CON, True, "复盘")
_def("chair", "主席", Camp.NEUTRAL, True, "报幕")
_def("timekeeper", "计时员", Camp.NEUTRAL, False, "计时")
_def("judge-logic", "逻辑评委", Camp.NEUTRAL, True, "打分")
_def("judge-content", "内容评委", Camp.NEUTRAL, True, "打分")
_def("judge-expression", "表达评委", Camp.NEUTRAL, True, "打分")


class AgentSpec(BaseModel):
    """一个 agent 位的装配结果。"""

    role_id: str
    camp: Camp
    position_cn: str
    system_prompt: str
    llm: bool = True


def _read(path: Path) -> str:
    if not path.exists():
        raise PromptAssetError(f"缺少资产文件: {path}")
    return path.read_text(encoding="utf-8")


def load_agents(
    assets_root: Path,
    role_ids: Iterable[str] | None = None,
    conduct_filename: str = "rules/conduct.md",
) -> dict[str, AgentSpec]:
    """加载角色：system_prompt = conduct 全文 + "---" + 角色文件全文（SPEC §7.3）。"""
    root = Path(assets_root)
    conduct = _read(root / conduct_filename).strip()
    selected = list(role_ids) if role_ids is not None else list(ROLE_DEFS)
    agents: dict[str, AgentSpec] = {}
    for role_id in selected:
        if role_id not in ROLE_DEFS:
            raise PromptAssetError(f"未知角色: {role_id}")
        role_def = ROLE_DEFS[role_id]
        text = _read(root / "roles" / f"{role_id}.md")
        for section in SECTIONS:
            if f"## {section}" not in text:
                raise PromptAssetError(f"{role_id}.md 缺少段落: ## {section}")
        for keyword in role_def.keywords:
            if keyword not in text:
                raise PromptAssetError(f"{role_id}.md 缺少义务关键词: {keyword}")
        agents[role_id] = AgentSpec(
            role_id=role_id,
            camp=role_def.camp,
            position_cn=role_def.position_cn,
            llm=role_def.llm,
            system_prompt=f"{conduct}\n\n---\n\n{text.strip()}",
        )
    return agents


def load_topics(assets_root: Path) -> list[Topic]:
    """加载内置辩题库 topics/topic-*.md（文件名 stem 即 topic_id）。"""
    directory = Path(assets_root) / "topics"
    if not directory.exists():
        raise PromptAssetError(f"缺少辩题目录: {directory}")
    topics = [_parse_topic(path) for path in sorted(directory.glob("topic-*.md"))]
    if not topics:
        raise PromptAssetError("辩题库为空")
    return topics


def _parse_topic(path: Path) -> Topic:
    lines = path.read_text(encoding="utf-8").splitlines()

    title = ""
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break
    if title.startswith("辩题："):
        title = title[len("辩题：") :].strip()

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
            continue
        if current and line.strip():
            sections[current].append(line.strip())

    def one(name: str) -> str:
        return "".join(sections.get(name, []))

    def bullets(name: str) -> list[str]:
        return [item.lstrip("-• ").strip() for item in sections.get(name, []) if item.strip()]

    return Topic(
        topic_id=path.stem,
        title=title,
        pro_stance=one("正方立场"),
        con_stance=one("反方立场"),
        pro_points=bullets("正方破题要点"),
        con_points=bullets("反方破题要点"),
    )
