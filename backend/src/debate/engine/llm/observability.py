"""可观测性（ISS-014 A-5）：trace_id 生成 + 结构化 LLM 调用日志。

设计要点：
- ``new_trace_id()``：一场比赛一个短 id（uuid4 前 12 位），CLI/Web/eval 同构；
- ``log_llm_call``：每次 LLM 请求（含每次重试尝试）输出一行 JSON 到 ``debate.llm``
  logger：event / trace_id / tier / model / attempt / ok / latency_ms /
  prompt_tokens / completion_tokens / error。**不记 prompt / response 全文**
  （隐私 + 容量：辩题与证据卡可能含用户信息，且一次调用可达 10k+ tokens）；
- 日志只作旁路观测：任何写日志失败都不得影响辩论主流程。

排查示例（"辩手突然集体复读"）：按 trace_id 过滤本场调用日志 → 看每位辩手每次
发言的 latency/tokens 与失败 attempt；token 异常低 / 大量 ContentFiltered / 长退避
可直接定位，不必翻 SSE 实录猜。
"""
from __future__ import annotations

import json
import logging
import uuid

logger = logging.getLogger("debate.llm")


def new_trace_id() -> str:
    """单场追踪 id（12 位十六进制，足够演示级唯一且日志/SSE 可读）。"""
    return uuid.uuid4().hex[:12]


def log_llm_call(
    *,
    trace_id: str,
    tier: str,
    model: str,
    attempt: int,
    ok: bool,
    latency_ms: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    error: str | None = None,
) -> None:
    """输出一行结构化日志（JSON）；旁路失败静默，绝不打断 LLM 调用链。"""
    try:
        record = {
            "event": "llm_call",
            "trace_id": trace_id,
            "tier": tier,
            "model": model,
            "attempt": attempt,
            "ok": ok,
            "latency_ms": round(latency_ms, 1),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        if error is not None:
            record["error"] = error  # 仅异常类型名，不记堆栈/响应体
        logger.info(json.dumps(record, ensure_ascii=False))
    except Exception:  # noqa: BLE001 —— 观测是旁路，日志故障绝不影响比赛
        pass
