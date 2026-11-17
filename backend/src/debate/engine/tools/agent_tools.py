"""辩手 Agent 工具层（v1.8）：LLM 经 function calling 主动调用工具共同生成发言。

治"语言固定相似"三大病灶之一——工具从"开赛预注入"升级为"临场取材"：
- search_evidence : 按本条发言的攻防点动态 BM25 检索证据卡（不再全场固定同 3 张）
- search_web      : 动态联网检索（provider 沿用 config.web_search，失败静默）
- check_argument  : Elenchus 论证自检（inproc 或 MCP stdio，与 config.tools.elenchus.mode 一致）
- check_time      : 查询本次发言剩余字数预算

降级纪律：工具层任何异常都不阻塞发言——run_debater_with_tools 整体失败时
回落为普通 complete（行为与 v1.7 完全一致）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..rag.retriever import detect_topic_domain, render_evidence
from ..rag.websearch import search_as_cards

if TYPE_CHECKING:
    from ..runtime import DebateContext

_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_evidence",
            "description": "在辩论证据库中检索与你当前论点/攻防点相关的证据卡（书籍、文献、报告）。每条发言建议检索一次，用具体关键词而非整句。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词，如'多任务切换 认知损耗'"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "联网搜索实时资料（新闻、数据、报告）。仅当你确实需要最新事实时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_argument",
            "description": "对你准备输出的论证段落做谬误自检（Elenchus 论证分析工具），返回检出的论证瑕疵，供你修改后再输出。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待自检的论证文本（≤600 字）"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_time",
            "description": "查询本次发言的剩余字数预算。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def build_tool_specs(ctx: "DebateContext") -> list[dict[str, Any]]:
    """按配置裁剪工具清单：elenchus 关闭则不下发 check_argument；无网络 provider 则不下发 search_web。"""
    specs = list(_TOOL_SPECS[:2])  # search_evidence + search_web
    if not (ctx.web_provider and ctx.web_provider != "none"):
        specs = [s for s in specs if s["function"]["name"] != "search_web"]
    if not (ctx.config.tools.elenchus.enabled and ctx.config.tools.agent.enabled):
        specs = [s for s in specs if s["function"]["name"] != "check_argument"]
    return specs


def _findings_text(result: dict) -> str:
    findings = result.get("findings") or []
    if not findings:
        return "论证自检通过：未检出明显谬误。"
    lines = []
    for f in findings[:4]:
        name = f.get("name", "?")
        quote = (f.get("quote") or "")[:50]
        explanation = (f.get("explanation") or "")[:120]
        lines.append(f"- {name}：引用“{quote}”——{explanation}")
    return "检出以下论证瑕疵（请修改表述或补强论证后再输出）：\n" + "\n".join(lines)


def execute_tool(ctx: "DebateContext", name: str, args: dict[str, Any], remaining_words: int) -> str:
    """工具执行入口；调用方已保证 name 合法，内部异常向上抛（由循环回注错误文本）。"""
    if name == "search_evidence":
        query = str(args.get("query", "")).strip()
        if not query:
            return "检索词为空，请给出具体关键词。"
        retriever = ctx.retriever
        if retriever is None:
            return "（本地证据检索不可用，请改用常识性论证）"
        domain = detect_topic_domain(
            f"{ctx.topic.title} {ctx.topic.pro_stance} {ctx.topic.con_stance}"
        )
        cards = retriever.search(query, top_k=3, topic_domain=domain)
        if not cards:
            return "证据库未检出相关卡片（宁缺毋滥）。请改用限定语表述常识性论据，不得虚构数据。"
        text = render_evidence(cards)
        return text.replace("【证据参考】（可引用其观点与来源名，不得虚构卡外精确数据）",
                            f"【检索结果：{query}】（可引用其观点与来源名，不得虚构卡外精确数据）")

    if name == "search_web":
        query = str(args.get("query", "")).strip()
        if not query:
            return "搜索词为空，请给出具体关键词。"
        cards = search_as_cards(query, top_k=3, provider=ctx.web_provider)
        if not cards:
            return "联网搜索无结果（或当前环境不可用）。请改用限定语表述，不得虚构数据。"
        return render_evidence(cards).replace("【证据参考】", f"【联网检索：{query}】")

    if name == "check_argument":
        text = str(args.get("text", "")).strip()
        if len(text) < 20:
            return "自检文本过短，请给出完整的论证段落。"
        el = ctx.config.tools.elenchus
        if not el.enabled:
            return "论证自检工具未启用。"
        from .elenchus import ElenchusUnavailable, analyze_one

        try:
            result = analyze_one(
                text[:600], check=el.check,
                timeout=ctx.config.tools.agent.tool_timeout_seconds,
                mode=el.mode,
            )
        except (ElenchusUnavailable, Exception):  # noqa: BLE001 —— 自检失败静默降级
            return "（论证自检工具暂不可用，请自行核验推理链条是否完整）"
        return _findings_text(result if isinstance(result, dict) else {})

    if name == "check_time":
        return f"本次发言剩余预算约 {remaining_words} 字，请据此控制篇幅。"

    raise ValueError(f"未知工具: {name}")


def tools_enabled_for(ctx: "DebateContext", phase_key: str | None) -> bool:
    """该环节是否启用工具循环：总开关 + 环节白名单 + 真实 OpenAI 通道（FakeLLM 无工具协议）。"""
    agent_cfg = ctx.config.tools.agent
    if not agent_cfg.enabled or phase_key not in agent_cfg.phases:
        return False
    if ctx.gateway.backend is not None and not hasattr(ctx.gateway.backend, "chat_with_tools"):
        return False  # 测试替身：无工具协议，回落单次调用
    return True


def tool_usage_instruction(specs: list[dict[str, Any]], remaining_words: int) -> str:
    """注入给辩手的工具使用说明（随任务指令拼接）。"""
    names = {s["function"]["name"] for s in specs}
    lines = [
        "【可用工具】你可以在正式发言前调用以下工具取材（调用后系统会把结果回传给你，再组织语言）：",
    ]
    if "search_evidence" in names:
        lines.append("- search_evidence(query)：检索辩论证据库（书籍/文献/报告），用具体关键词。")
    if "search_web" in names:
        lines.append("- search_web(query)：联网搜索实时资料。")
    if "check_argument" in names:
        lines.append("- check_argument(text)：对核心论证段做谬误自检，检出的瑕疵需修改后再输出。")
    lines.append(f"- check_time()：查询剩余字数（当前约 {remaining_words} 字）。")
    lines.append("工具结果只作素材：引用须标注来源名，不得虚构工具之外的数据；无需工具时直接输出正文。")
    lines.append("各辩位取材重点不同，按你角色文件「行为规则」中的工具取材策略执行"
                 "（立论检索定义与判准支撑、驳论检索对方论点反证、攻辩先检索再设计数据逼问、总结自检归谬段）。")
    return "\n".join(lines)


def run_debater_with_tools(
    ctx: "DebateContext",
    *,
    messages: list[dict[str, str]],
    instruction: str,
    remaining_words: int,
) -> tuple[Any, bool]:
    """带工具循环的辩手调用；返回 (LLMResult, used_tools)。

    任何异常 → 回落普通 complete（used_tools=False），与 v1.7 行为完全一致。
    """
    specs = build_tool_specs(ctx)
    augmented = messages
    if specs:
        augmented = [dict(m) for m in messages]
        augmented[-1] = {**augmented[-1],
                         "content": f"{augmented[-1]['content']}\n\n{tool_usage_instruction(specs, remaining_words)}"}
    try:
        result = ctx.gateway.chat_with_tools(
            augmented,
            tools=specs,
            execute=lambda name, args: execute_tool(ctx, name, args, remaining_words),
            tier="debater",
            temperature=ctx.config.model.temperature.debater,
            max_tokens=int(remaining_words * 1.6) + 128,
            max_rounds=ctx.config.tools.agent.max_tool_rounds,
        )
        return result, True
    except Exception:  # noqa: BLE001 —— 工具循环整体失败：回落单次调用
        return ctx.gateway.complete(
            augmented, tier="debater",
            temperature=ctx.config.model.temperature.debater,
            max_tokens=int(remaining_words * 1.6) + 64,
        ), False
