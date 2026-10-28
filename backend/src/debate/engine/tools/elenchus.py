"""Elenchus 接入（v1.6）：苏格拉底式论证分析——谬误检测辅助逻辑评委裁决。

双模式（config.tools.elenchus.mode）：
- inproc（默认）：进程内直调 elenchus.core.analyzer.analyze_text（稳定、零进程开销）
- mcp：真 MCP 协议路径——手写 stdio JSON-RPC 客户端起 `elenchus mcp` 子进程
  （initialize → tools/call analyze；不依赖 mcp SDK，协议本身即 JSON lines）

LLM 配置：复用 .env 的 OPENAI_API_KEY/OPENAI_BASE_URL（GLM 兼容端点，免费档）。
降级纪律：未安装 / 超时 / 任何异常 → 返回空（评委回落纯 LLM 评审，绝不阻塞比赛）。
安装：uv pip install ./third_party/elenchus（源码已随库，GitHub 被墙时免联网）。
"""
from __future__ import annotations

import concurrent.futures
import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..schema import CAMP_CN, Camp, Speech


def _configure_provider_env() -> None:
    """elenchus 走 OpenAI 兼容 provider（复用 GLM 免费档）。"""
    os.environ.setdefault("LLM_PROVIDER", "openai-compatible")
    os.environ.setdefault("LLM_MODEL", os.environ.get("MODEL_OFFICIAL", "glm-4.5-flash"))


class ElenchusUnavailable(Exception):
    pass


def _analyze_inproc(text: str, check: str, timeout: float) -> dict:
    """超时即放弃——**绝不能用 `with ThreadPoolExecutor`**。

    `with` 退出时执行 `shutdown(wait=True)`：若 elenchus 内部的 LLM 调用卡在网络上
    （无超时、连接假死），future.cancel() 杀不掉已运行的线程，主线程会**永久阻塞**
    在 `__exit__` 上——这正是"评委点评环节卡死"的直接成因之一。
    改为显式 shutdown(wait=False, cancel_futures=True)：超时立即抛错返回，卡住的线程
    留作守护性泄漏（进程退出即回收），绝不拖住评分环节。
    """
    from elenchus.core.analyzer import analyze_text

    _configure_provider_env()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="elenchus")
    try:
        future = pool.submit(analyze_text, text, check=check, sensitivity=None)
        return future.result(timeout=timeout)
    except TimeoutError:
        future.cancel()  # 排队中的任务取消；已运行的线程无法强杀，交由不等待的 shutdown
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


class _McpStdioClient:
    """极简 MCP stdio 客户端（JSON-RPC 2.0 over lines）——零 SDK 依赖。"""

    def __init__(self, timeout: float = 20.0):
        import subprocess
        import sys

        self._proc = subprocess.Popen(
            [sys.executable, "-m", "elenchus.cli", "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", env={**os.environ},
        )
        self._timeout = timeout
        self._id = 0
        self._start()

    def _send(self, method: str, params: dict | None = None) -> dict:
        import threading

        self._id += 1
        request = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        self._proc.stdin.write(json.dumps(request) + "\n")
        self._proc.stdin.flush()
        # 逐行读到匹配 id 的响应（跳过通知行）
        result: dict[str, Any] | None = None

        def reader() -> None:
            nonlocal result
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("id") == self._id:
                    result = data
                    return

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(self._timeout)
        if result is None:
            raise ElenchusUnavailable(f"MCP 响应超时: {method}")
        if "error" in result:
            raise ElenchusUnavailable(f"MCP 错误: {result['error']}")
        return result.get("result", {})

    def _start(self) -> None:
        self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "debate-engine", "version": "1.0"},
        })
        self._proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self._proc.stdin.flush()

    def call_analyze(self, text: str, check: str) -> dict:
        result = self._send("tools/call", {
            "name": "analyze",
            "arguments": {"text": text, "check": check, "sensitivity": None},
        })
        content = (result.get("content") or [{}])[0].get("text", "{}")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"overall": str(content), "findings": []}

    def close(self) -> None:
        try:
            self._proc.stdin.close()
            self._proc.terminate()
        except Exception:  # noqa: BLE001
            pass


def analyze_one(text: str, check: str = "fallacy", timeout: float = 45.0,
                mode: str = "inproc", client: _McpStdioClient | None = None) -> dict:
    """单条分析；inproc 走库，mcp 走 stdio 子进程。"""
    if mode == "mcp":
        if client is None:
            raise ElenchusUnavailable("mcp 模式需传入已建立的 client")
        return client.call_analyze(text, check)
    return _analyze_inproc(text, check, timeout)


def _collect_findings(speech: Speech, result: dict, findings_lines: list[str], marks: list[dict]) -> None:
    """把单条分析结果归并为注入行与 marks（主线程调用，无锁竞争）。"""
    for f in (result.get("findings") or [])[:4]:
        name = f.get("name", "?")
        quote = (f.get("quote") or "")[:60]
        conf = f.get("confidence", "?")
        findings_lines.append(
            f"- [{CAMP_CN[speech.camp]}·{speech.speaker}] {name}（{conf}）：引用“{quote}”"
        )
        marks.append({"seq": speech.seq, "camp": speech.camp.value, "speaker": speech.speaker,
                      "name": name, "quote": quote, "explanation": (f.get("explanation") or "")[:160],
                      "confidence": conf})


def analyze_speeches_detailed(speeches: list[Speech], *, per_camp: int = 2, check: str = "fallacy",
                              timeout: float = 45.0, mode: str = "inproc",
                              total_budget: float | None = None,
                              parallel: int = 1,
                              on_progress: Any = None) -> tuple[str, list[dict]]:
    """对双方核心论点（每方最长的 per_camp 条辩手发言）做谬误检测，渲染注入段。

    返回 (注入段文本, marks)；marks=[{seq,camp,speaker,name,quote,explanation,confidence}]（v1.7 SSE/落库用）。

    v1.9.2 起新增：
    - total_budget：整段质检的总时长上限（默认 2×timeout）。逐条消耗，预算耗尽即停——
      避免 per_camp=2 时 4 条 × 45s = 3 分钟静默，评委迟迟不开始；
    - on_progress(done, total)：每条分析前回调，供节点向前端播报进度。

    v2.4 新增 parallel（仅 inproc 模式生效）：多条核心论点交给线程池同时分析，
    质检墙钟时间从 Σ单条 压到 ≈最慢单条。预算纪律不变：as_completed 按 total_budget
    收割，超时未完成的直接放弃（对应线程由 shutdown(wait=False) 遗弃，不阻塞调用方）。
    """
    try:
        from elenchus.core.analyzer import analyze_text  # noqa: F401 —— 可用性探测
    except ImportError:
        return "", []

    picked = _pick_core(speeches, per_camp)
    if not picked:
        return "", []

    import time as _time

    budget = total_budget if total_budget and total_budget > 0 else timeout * 2
    deadline = _time.monotonic() + budget
    client: _McpStdioClient | None = None
    findings_lines: list[str] = []
    marks: list[dict] = []
    use_parallel = parallel > 1 and mode == "inproc" and len(picked) > 1

    def notify(done: int) -> None:
        if on_progress is not None:
            try:
                on_progress(done, len(picked))
            except Exception:  # noqa: BLE001 —— 进度回调失败不影响分析
                pass

    try:
        if mode == "mcp":
            client = _McpStdioClient(timeout=timeout)  # stdio 子进程单管道，天然串行
        if use_parallel:
            workers = max(1, min(int(parallel), len(picked)))
            pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="elenchus-qc")
            futures = {
                pool.submit(analyze_one, s.text, check=check, timeout=timeout, mode=mode): s
                for s in picked
            }
            done_count = 0
            try:
                # 预算收割：到点即停，未完成的放弃（线程遗弃由 shutdown(wait=False) 处理）
                for fut in concurrent.futures.as_completed(futures, timeout=budget):
                    speech = futures[fut]
                    try:
                        result = fut.result()
                    except Exception:  # noqa: BLE001 —— 单条失败跳过
                        continue
                    done_count += 1
                    notify(done_count - 1)
                    _collect_findings(speech, result, findings_lines, marks)
            except concurrent.futures.TimeoutError:
                pass  # 预算耗尽：已完成的照常采用
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        else:
            for idx, speech in enumerate(picked):
                remaining = deadline - _time.monotonic()
                if remaining <= 1.0:
                    break  # 预算耗尽：守卫评委阶段不被质检拖住
                notify(idx)
                try:
                    result = analyze_one(speech.text, check=check, timeout=min(timeout, remaining),
                                         mode=mode, client=client)
                except Exception:  # noqa: BLE001 —— 单条失败跳过
                    continue
                _collect_findings(speech, result, findings_lines, marks)
    except Exception:  # noqa: BLE001 —— 整体失败降级
        return "", []
    finally:
        if client is not None:
            client.close()

    if not findings_lines:
        return "", []
    text = ("【机器论证分析】（Elenchus 工具检测，仅供参考——每条须你自行核验引用与结论后才可采信）\n"
            + "\n".join(findings_lines))
    return text, marks


def analyze_speeches(speeches: list[Speech], **kwargs) -> str:
    """兼容旧签名：仅返回注入段文本。"""
    return analyze_speeches_detailed(speeches, **kwargs)[0]




def _pick_core(speeches: list[Speech], per_camp: int) -> list[Speech]:
    """每方取最长的 per_camp 条辩手发言（立论/结辩最可能是核心论证载体）。"""
    picked: list[Speech] = []
    for camp in (Camp.PRO, Camp.CON):
        camp_speeches = [s for s in speeches if s.camp is camp and len(s.text) >= 40]
        camp_speeches.sort(key=lambda s: len(s.text), reverse=True)
        picked.extend(camp_speeches[:per_camp])
    return picked
