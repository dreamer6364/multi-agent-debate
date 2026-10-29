"""配置加载（SPEC §2）。config.yaml 字段锁死，密钥一律走 .env，不入库。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class PhasesConfig(BaseModel):
    opening: float = 1.0
    statement: float = 3.0
    cross_examination: float = 1.5
    rebuttal: float = 2.0
    attack: float = 2.0
    attack_summary: float = 1.5
    free_debate: float = 4.0
    closing: float = 3.0
    # v2.2：主席结束致辞（phase 11）——只用于导出口径，不计入赛制时长
    closing_ceremony: float = 1.5


class ProcedureConfig(BaseModel):
    speed_cpm: int = 220
    phases: PhasesConfig = PhasesConfig()
    free_debate_turn_cap: float = 0.5


class TemperatureConfig(BaseModel):
    debater: float = 0.85
    judge: float = 0.3
    chair: float = 0.3
    digest: float = 0.2


class ModelConfig(BaseModel):
    temperature: TemperatureConfig = TemperatureConfig()
    max_retries: int = 3
    timeout_seconds: int = 60
    min_call_interval: float = 15.0  # 全局限速秒（免费档 RPM 保护；0=不限；MIN_CALL_INTERVAL 环境变量可覆盖）
    degradation_limit: int = 5  # 连续降级熔断阈值（免费档建议 5：带占位跑完优于中止）


class ContextConfig(BaseModel):
    recent_speeches: int = 6
    max_input_tokens: int = 4000
    digest_max_chars: int = 1200


class JudgesConfig(BaseModel):
    count: int = 3
    timeout_seconds: float = 240.0  # 单评委评审总时长上限（超时按规则弃权，绝不阻塞评分环节）
    heartbeat_seconds: float = 10.0  # 评审期进度心跳间隔（前端据此显示"已评议 Xs"，也是卡死自检信号）
    # v2.4：评审期临时覆盖全局限速间隔。评委只有 3 次调用，不需要辩论期那么保守的 15s 间隔——
    # 3 位评委并行时按 15s 排队会白白多等 ~30s。v2.6 默认 0 = 完全并行（3 次调用同时发出，
    # 重试由 LLM 延迟自然错开）；免费档如遇 429 风暴可回调到 2~5。
    throttle_seconds: float | None = 0.0
    # v2.6：逻辑评委等质检结论注入提示词的上限（原硬编码 20s）。质检为进程内分析通常秒级
    # 完成；到点等不到就先评审，谬误标注完成后仍会以 fallacy 事件挂到质检报告。
    qc_join_wait_seconds: float = 10.0
    # v2.6：评审收尾后补收割质检标注的上限（原硬编码 30s）。到点即宣判，绝不拖赛果。
    qc_final_wait_seconds: float = 10.0

    @field_validator("count")
    @classmethod
    def _count_allowed(cls, value: int) -> int:
        if value not in (1, 3, 5):
            raise ValueError("judges.count 仅允许 1 / 3 / 5")
        return value


class EngineConfig(BaseModel):
    """引擎总时限（v1.9.2）：任何单点卡死都不允许场次永远停在 running。

    - max_duration_seconds：整场（全部环节 + 质检 + 评审 + 宣判）硬上限，到点置取消旗标；
    - cancel_grace_seconds：置旗标后等引擎在节点边界退出的宽限，超时按 failed 收尾；
    - drain_timeout_seconds：收尾时落库队列的排空上限（drain 任务异常也不会永久挂起）。
    """

    max_duration_seconds: float = 5400.0
    cancel_grace_seconds: float = 90.0
    drain_timeout_seconds: float = 15.0


class QuotaConfig(BaseModel):
    concurrent_debates_per_user: int = 1  # 并发限 1 场；每日场次不限（v1.3 修订：删除每日配额）


class ConductConfig(BaseModel):
    abuse_keywords: list[str] = Field(default_factory=list)
    max_interrupts_per_answer: int = 2


class RagConfig(BaseModel):
    enabled: bool = True
    top_k: int = 3
    knowledge_dir: str = "knowledge"


class ElenchusConfig(BaseModel):
    enabled: bool = True
    mode: str = "inproc"      # inproc（进程内直调，稳定）| mcp（stdio JSON-RPC 子进程）
    check: str = "fallacy"    # fallacy / rhetoric / bias / scam / all
    # v2.6 严格度档位（透传 elenchus）：lenient 只报高置信 / balanced（库默认）/
    # strict 连低置信的细微问题也报并标 low confidence（最多 7 条）。
    sensitivity: str = "strict"
    # v2.8.1 流式质检：每条辩手发言落定即分析（边辩边检），评审阶段只收割现成结果；
    # 关闭则回落「评审阶段批量分析」的旧行为。仅 inproc 模式生效。
    stream: bool = True
    per_camp: int = 2         # 每方分析的核心论点数（批量模式/流式关闭时生效）
    timeout_seconds: float = 45.0
    total_budget_seconds: float = 90.0  # 质检总预算：耗尽即停，绝不拖住评委阶段
    parallel: int = 4         # v2.6：多条核心论点并行分析度（1 = 顺序；mcp 模式强制顺序）。
                              # per_camp=2 时 4 条全并行，墙钟 ≈ 最慢单条，而非 Σ单条


class GoldenQuoteConfig(BaseModel):
    """流式金句摘录（v2.9）：每条辩手发言落定即由轻量 LLM 摘金句（失败静默降级）。"""

    enabled: bool = True
    timeout_seconds: float = 30.0   # 单条摘录超时
    parallel: int = 2               # 并行摘录度（1 = 顺序）
    max_per_speech: int = 2         # 单条发言最多摘录条数
    max_total: int = 16             # 全场金句总数封顶（宁缺毋滥）
    min_len: int = 60               # 参与摘录的最短发言字数


class AgentToolsConfig(BaseModel):
    """辩手 Agent 工具循环（v1.8）：模型经 function calling 主动调工具共同生成发言。"""

    enabled: bool = True
    max_tool_rounds: int = 2          # 每条发言最多工具往返轮数（控制免费档 RPM 成本）
    tool_timeout_seconds: float = 15.0
    # 启用工具循环的环节（自由辩/质询为短打高频环节，默认不开以控成本；动态证据仍全局生效）
    phases: list[str] = ["statement", "rebuttal", "attack_summary", "closing"]


class ToolsConfig(BaseModel):
    """运行时工具层（v1.6）：Elenchus 论证分析等，全部静默降级。"""

    elenchus: ElenchusConfig = ElenchusConfig()
    agent: AgentToolsConfig = AgentToolsConfig()
    golden: GoldenQuoteConfig = GoldenQuoteConfig()


class WebSearchConfig(BaseModel):
    enabled: bool = True
    top_k: int = 3
    # provider：env（SEARCH_PROVIDER / TAVILY_API_KEY 自动探测）| zhipu | tavily | ddgs | none
    provider: str = "env"


class StreamConfig(BaseModel):
    enable_delta: bool = True  # speech_delta 流式直播（v2.1）


class ExportConfig(BaseModel):
    output_dir: str = "debate"


class AppConfig(BaseModel):
    procedure: ProcedureConfig = ProcedureConfig()
    model: ModelConfig = ModelConfig()
    context: ContextConfig = ContextConfig()
    judges: JudgesConfig = JudgesConfig()
    engine: EngineConfig = EngineConfig()
    quota: QuotaConfig = QuotaConfig()
    conduct: ConductConfig = ConductConfig()
    rag: RagConfig = RagConfig()
    web_search: WebSearchConfig = WebSearchConfig()
    tools: ToolsConfig = ToolsConfig()
    stream: StreamConfig = StreamConfig()
    export: ExportConfig = ExportConfig()


def deep_merge(base: dict, overrides: dict) -> dict:
    """递归合并（overrides 优先），用于剧本/测试对赛制参数的覆盖。"""
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: str | Path) -> AppConfig:
    """加载 config.yaml；文件缺失或字段非法立即抛错（fail-fast）。"""
    file = Path(path)
    if not file.exists():
        raise FileNotFoundError(f"配置文件不存在: {file}")
    data: dict[str, Any] = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)


def load_env(path: str | Path) -> None:
    """加载 .env（存在时）；缺失为正常场景（M1 无需密钥）。"""
    file = Path(path)
    if file.exists():
        from dotenv import load_dotenv

        load_dotenv(file, override=False)
