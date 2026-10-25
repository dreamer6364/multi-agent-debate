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

    @field_validator("count")
    @classmethod
    def _count_allowed(cls, value: int) -> int:
        if value not in (1, 3, 5):
            raise ValueError("judges.count 仅允许 1 / 3 / 5")
        return value


class QuotaConfig(BaseModel):
    concurrent_debates_per_user: int = 1  # 并发限 1 场；每日场次不限（v1.3 修订：删除每日配额）


class ConductConfig(BaseModel):
    abuse_keywords: list[str] = Field(default_factory=list)
    max_interrupts_per_answer: int = 2


class RagConfig(BaseModel):
    enabled: bool = True
    top_k: int = 3
    knowledge_dir: str = "knowledge"


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
    quota: QuotaConfig = QuotaConfig()
    conduct: ConductConfig = ConductConfig()
    rag: RagConfig = RagConfig()
    web_search: WebSearchConfig = WebSearchConfig()
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
