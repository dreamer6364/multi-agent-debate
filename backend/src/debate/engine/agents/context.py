"""Agent 上下文组装（SPEC §4.4 / AGENT_CONSTRAINTS §6 可见性矩阵）。

五段注入：system(conduct+role) → 辩题与立场 → digest → 最近 K 条原文 → 环节任务。
立场信息隔离：辩手只拿本方破题要点；评委拿双方 + 摘录池。
超限裁剪顺序：④最近原文 → ③digest → ②要点（SPEC §4.4 修订口径，见模块注）。
"""
from __future__ import annotations

from ..schema import CAMP_CN, Camp, Speech, Topic
from .loader import AgentSpec


def estimate_tokens(text: str) -> int:
    """估算口径：中文 1 字 ≈ 1 token（偏保守，仅用于输入预算控制）。"""
    return max(1, len(text))


def _stance_of(topic: Topic, camp: Camp) -> str:
    return topic.stance_for(camp)


def _points_of(topic: Topic, camp: Camp) -> list[str]:
    return topic.points_for(camp)


def _points_block(topic: Topic, camp: Camp) -> str:
    # v2.9.1 辩题锚定：辩手 prompt 必须显式携带辩题全文——只有"本方立场+要点"时，
    # 弱模型会被证据卡议题带偏（实测：爱人辩题全程被科技证据带成 AI 依赖攻防）。
    lines = [
        f"【辩题】{topic.title}",
        "【辩题锚定（最高优先级）】本场辩题只有这一个，全场所有发言必须严格围绕它展开；"
        "证据卡只是论证素材库，若其主题与辩题分属不同领域，禁止采纳其议题与立场，只可借鉴论证方法。",
        f"【本方立场】{_stance_of(topic, camp)}",
        "【本方破题要点】",
    ]
    lines += [f"- {p}" for p in _points_of(topic, camp)]
    return "\n".join(lines)


def _recent_block(recent: list[Speech]) -> str:
    if not recent:
        return "【最近发言】\n（暂无）"
    lines = [f"[{s.seq}][{CAMP_CN[s.camp]}·{s.speaker}] {s.text}" for s in recent]
    return "【最近发言】\n" + "\n".join(lines)


def _assemble(system: str, blocks: list[str]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(blocks)},
    ]


def _total_tokens(messages: list[dict[str, str]]) -> int:
    return sum(estimate_tokens(m["content"]) for m in messages)


def build_debater_messages(
    *,
    spec: AgentSpec,
    topic: Topic,
    digest_text: str,
    recent: list[Speech],
    instruction: str,
    recent_k: int = 6,
    max_input_tokens: int | None = None,
    evidence_text: str = "",
) -> list[dict[str, str]]:
    """辩手调用消息（v1.4 六段注入：立场 → 证据参考 → 摘要 → 最近原文 → 任务）。
    超预算裁剪顺序：④最近原文 → ③digest → ②要点 → ⑤证据（证据段最后裁）。"""
    recent = list(recent[-recent_k:]) if recent_k > 0 else []

    def assemble(points: list[str], digest: str, recent_part: list[Speech], ev: str) -> list[dict[str, str]]:
        blocks = [_points_block(topic, spec.camp)]
        if ev:
            blocks.append(ev)
        blocks += [
            f"【战局摘要】\n{digest}" if digest else "【战局摘要】\n（比赛刚开始）",
            _recent_block(recent_part),
            f"【本环节任务】\n{instruction}",
        ]
        return _assemble(spec.system_prompt, blocks)

    if max_input_tokens is None:
        return assemble(_points_of(topic, spec.camp), digest_text, recent, evidence_text)

    current_points = list(_points_of(topic, spec.camp))
    current_digest = digest_text
    current_recent = recent
    current_ev = evidence_text
    messages = assemble(current_points, current_digest, current_recent, current_ev)
    guard = 0
    while _total_tokens(messages) > max_input_tokens and guard < 8:
        guard += 1
        if current_recent:
            current_recent = []  # 第一刀：砍最近原文
        elif len(current_digest) > 200:
            current_digest = current_digest[: len(current_digest) // 2]  # 第二刀：压缩摘要
        elif len(current_points) > 1:
            current_points = current_points[:1]  # 第三刀：要点只留第一条
        elif current_ev:
            current_ev = ""  # 第四刀（v1.4）：证据段最后裁
        else:
            break
        messages = assemble(current_points, current_digest, current_recent, current_ev)
    return messages


def build_chair_messages(
    *, spec: AgentSpec, topic: Topic, instruction: str
) -> list[dict[str, str]]:
    """主席调用消息（v1.9.2 修复）：只注入辩题与双方立场句。

    历史缺陷：主席也用 build_debater_messages，而 NEUTRAL 角色的
    stance_for/points_for 均返回空，提示词里**根本没有辩题标题**，
    引题演讲只能凭空发挥，于是与辩题脱节。
    可见性矩阵：主席可见「辩题」与双方公开立场，仍不可见破题要点/战局/原文。
    """
    blocks = [
        f"【辩题】{topic.title}",
        f"【正方立场】{topic.pro_stance or '（未提供）'}",
        f"【反方立场】{topic.con_stance or '（未提供）'}",
        f"【本环节任务】\n{instruction}",
    ]
    return _assemble(spec.system_prompt, blocks)


def build_judge_messages(
    *,
    spec: AgentSpec,
    topic: Topic,
    digest_text: str,
    excerpts: list,
    checklist: str,
    instruction: str,
    machine_analysis_text: str = "",
    conduct_records: str = "",
    evidence_sources: str = "",
) -> list[dict[str, str]]:
    """评委调用消息：双方立场与要点 + digest + 原文摘录池 + 各环节要点清单。

    v2.10 评委证据链补全（P0-2）：
    - ``conduct_records``：本场违规/纪律旁注清单（人身攻击、打断违规、回避追问、超时叫停）。
      表达评委的风度纪律分须以它为直接扣分依据（此前它只写在角色文件里，评委却看不到记录）；
    - ``evidence_sources``：平台注入给辩手的证据卡来源白名单。内容评委据此区分"平台给的真证据"
      与"辩手编造的引用"（守则三允许引用这些来源，故不得当作虚构数据判负）。
    """
    both = (
        f"【正方立场】{topic.pro_stance}\n【正方破题要点】\n"
        + "\n".join(f"- {p}" for p in topic.pro_points)
        + "\n\n"
        + f"【反方立场】{topic.con_stance}\n【反方破题要点】\n"
        + "\n".join(f"- {p}" for p in topic.con_points)
    )
    pool_lines = [
        f"- [P{e.phase}][{CAMP_CN[e.camp]}·{e.speaker}] {e.text}" for e in excerpts
    ]
    blocks = [
        f"【辩题】{topic.title}",
        both,
        f"【战局摘要】\n{digest_text}",
        "【原文摘录】\n" + ("\n".join(pool_lines) if pool_lines else "（暂无）"),
    ]
    if machine_analysis_text:
        blocks.append(machine_analysis_text)  # v1.6 Elenchus 机器论证分析（仅逻辑评委）
    if conduct_records:
        blocks.append(
            "【本场违规与纪律记录】（代码侧检测的旁注）「数据待查」是**核验线索**："
            "内容评委须比对上下文与常识后判定真伪，查实虚构才可判该论点作废，未查实不得扣分；"
            "其余条目（人身攻击/打断违规/回避追问/超时）是风度纪律分的直接依据。\n" + conduct_records
        )
    if evidence_sources:
        blocks.append(
            "【平台注入的证据来源白名单】（守则三允许辩手引用以下来源；"
            "引用它们不属于虚构数据，不得据此判该论点作废）\n" + evidence_sources
        )
    blocks += [
        f"【各环节要点清单】\n{checklist}",
        f"【评审任务】\n{instruction}",
    ]
    return _assemble(spec.system_prompt, blocks)
