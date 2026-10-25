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
    from elenchus.core.analyzer import analyze_text

    _configure_provider_env()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(analyze_text, text, check=check, sensitivity=None)
        return future.result(timeout=timeout)


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


def analyze_speeches(speeches: list[Speech], *, per_camp: int = 2, check: str = "fallacy",
                     timeout: float = 45.0, mode: str = "inproc") -> str:
    """对双方核心论点（每方最长的 per_camp 条辩手发言）做谬误检测，渲染注入段。

    返回空串表示降级（未安装/全失败）——调用方据此跳过注入。
    """
    try:
        from elenchus.core.analyzer import analyze_text  # noqa: F401 —— 可用性探测
    except ImportError:
        return ""

    picked = _pick_core(speeches, per_camp)
    if not picked:
        return ""

    client: _McpStdioClient | None = None
    findings_lines: list[str] = []
    try:
        if mode == "mcp":
            client = _McpStdioClient(timeout=timeout)
        for speech in picked:
            try:
                result = analyze_one(speech.text, check=check, timeout=timeout, mode=mode, client=client)
            except Exception:  # noqa: BLE001 —— 单条失败跳过
                continue
            for f in (result.get("findings") or [])[:4]:
                name = f.get("name", "?")
                quote = (f.get("quote") or "")[:60]
                conf = f.get("confidence", "?")
                findings_lines.append(
                    f"- [{CAMP_CN[speech.camp]}·{speech.speaker}] {name}（{conf}）：引用“{quote}”"
                )
    except Exception:  # noqa: BLE001 —— 整体失败降级
        return ""
    finally:
        if client is not None:
            client.close()

    if not findings_lines:
        return ""
    return ("【机器论证分析】（Elenchus 工具检测，仅供参考——每条须你自行核验引用与结论后才可采信）\n"
            + "\n".join(findings_lines))


def _pick_core(speeches: list[Speech], per_camp: int) -> list[Speech]:
    """每方取最长的 per_camp 条辩手发言（立论/结辩最可能是核心论证载体）。"""
    picked: list[Speech] = []
    for camp in (Camp.PRO, Camp.CON):
        camp_speeches = [s for s in speeches if s.camp is camp and len(s.text) >= 40]
        camp_speeches.sort(key=lambda s: len(s.text), reverse=True)
        picked.extend(camp_speeches[:per_camp])
    return picked
