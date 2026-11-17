"""A-1 冒烟诊断驱动（归档 issues/evidence/）：给 Gateway 打补丁打印每次调用失败原因，再跑 eval。

背景：首次冒烟比赛在第 6 条发言处"连续 5 次 LLM 降级熔断"，但报告不记录
底层异常（LLMExhausted 吞掉了 last_exc）。本脚本在不改引擎代码的前提下，
把 complete/stream 的每次异常打出来，确认失败是限流、超时还是内容拦截。

用法（backend/ 目录）：
    .venv/Scripts/python.exe -X utf8 ../issues/evidence/_diag_a1_smoke.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND))

from debate.engine.llm import gateway as gw_mod  # noqa: E402

_orig_complete = gw_mod.Gateway.complete
_orig_stream = gw_mod.Gateway.stream
_t0 = time.monotonic()


def _stamp() -> str:
    return f"+{time.monotonic() - _t0:7.1f}s"


def _spy_complete(self, messages, **kw):
    try:
        result = _orig_complete(self, messages, **kw)
        print(f"[{_stamp()}] complete OK tier={kw.get('tier')} "
              f"tok={result.prompt_tokens}/{result.completion_tokens}", flush=True)
        return result
    except Exception as exc:  # noqa: BLE001 —— 诊断脚本：原样打印后放行
        print(f"[{_stamp()}] complete FAIL tier={kw.get('tier')} "
              f"{type(exc).__name__}: {str(exc)[:400]}", flush=True)
        raise


def _spy_stream(self, messages, **kw):
    try:
        yield from _orig_stream(self, messages, **kw)
    except Exception as exc:  # noqa: BLE001
        print(f"[{_stamp()}] stream FAIL {type(exc).__name__}: {str(exc)[:400]}", flush=True)
        raise


gw_mod.Gateway.complete = _spy_complete
gw_mod.Gateway.stream = _spy_stream

from eval.run_eval import main  # noqa: E402

if __name__ == "__main__":
    code = main(["--topics", "g01", "--format", "lite", "--label", "smoke2"])
    print(f"[diag] eval exit={code}", flush=True)
    raise SystemExit(code)
