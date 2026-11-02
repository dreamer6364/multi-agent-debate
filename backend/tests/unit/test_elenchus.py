"""Elenchus 接入测试（v1.6）：注入格式、静默降级、逻辑评委独占注入。"""
from debate.engine.agents.context import build_judge_messages
from debate.engine.agents.loader import load_agents, load_topics
from debate.engine.schema import Camp, Speech
from debate.engine.sim.digest import Digest, render_digest


def _speech(seq, camp, text, speaker="pro-1"):
    return Speech(seq=seq, phase=2, phase_name="开篇立论", speaker=speaker,
                  camp=camp, text=text, word_count=len(text))


def test_analyze_speeches_renders_findings(monkeypatch):
    from debate.engine.tools import elenchus as et

    def fake_analyze(text, check="fallacy", timeout=45.0, sensitivity=None):
        assert check == "fallacy"
        return {"overall": "x", "findings": [
            {"name": "Ad hominem", "quote": "你反对就是嫉妒", "explanation": "e", "confidence": "high"},
        ]}

    monkeypatch.setattr(et, "_analyze_inproc", fake_analyze)
    speeches = [_speech(1, Camp.PRO, "长篇立论" * 20), _speech(2, Camp.CON, "对方立论" * 20, "con-1")]
    text = et.analyze_speeches(speeches, mode="inproc")
    assert text.startswith("【机器论证分析】")
    assert "仅供参考" in text  # 工具检测 ≠ 评委结论
    assert "Ad hominem" in text and "你反对就是嫉妒" in text
    assert "[正方·pro-1]" in text and "[反方·con-1]" in text


def test_analyze_speeches_silent_when_not_installed(monkeypatch):
    import sys as _sys
    from debate.engine.tools import elenchus as et

    monkeypatch.setitem(_sys.modules, "elenchus", None)  # None 条目 → import 即 ImportError
    monkeypatch.setitem(_sys.modules, "elenchus.core", None)
    monkeypatch.setitem(_sys.modules, "elenchus.core.analyzer", None)
    assert et.analyze_speeches([_speech(1, Camp.PRO, "x" * 60)]) == ""


def test_analyze_speeches_per_failure_skipped(monkeypatch):
    from debate.engine.tools import elenchus as et

    calls = {"n": 0}

    def flaky(text, check="fallacy", timeout=45.0, sensitivity=None):
        calls["n"] += 1
        raise RuntimeError("这条炸了")

    monkeypatch.setattr(et, "_analyze_inproc", flaky)
    out = et.analyze_speeches([_speech(1, Camp.PRO, "x" * 60), _speech(2, Camp.CON, "y" * 60, "con-1")])
    assert out == "" and calls["n"] == 2  # 全失败 → 空段（降级）


def test_inproc_timeout_does_not_wait_for_hung_thread(monkeypatch):
    """回归（v1.9.2）：`with ThreadPoolExecutor` 退出会 join 线程 → 内部调用假死时**永久阻塞**。

    修复后超时必须立即返回（旧写法会等满 sleep 时长），这是"评委环节卡死"的成因之一。
    """
    import sys as _sys
    import time as _time
    import types as _types

    mod = _types.ModuleType("elenchus.core.analyzer")

    def hang(text, check="fallacy", sensitivity=None):
        _time.sleep(5)
        return {"findings": []}

    mod.analyze_text = hang
    monkeypatch.setitem(_sys.modules, "elenchus.core.analyzer", mod)

    from debate.engine.tools import elenchus as et

    start = _time.monotonic()
    try:
        et._analyze_inproc("一段论证", "fallacy", 0.2)
        raise AssertionError("应当超时")
    except TimeoutError:
        pass
    elapsed = _time.monotonic() - start
    assert elapsed < 1.5, f"超时后仍在等待挂死线程：{elapsed:.1f}s"


def test_analyze_speeches_total_budget_stops_early(monkeypatch):
    """质检总预算耗尽即停：per_camp=2 时不会把 4 条全部按 45s 跑满。"""
    from debate.engine.tools import elenchus as et

    called = {"n": 0}

    def slow(text, check="fallacy", timeout=45.0, sensitivity=None):
        called["n"] += 1
        import time as _t
        _t.sleep(0.5)
        return {"findings": [{"name": "X", "quote": "q", "explanation": "e", "confidence": "low"}]}

    monkeypatch.setattr(et, "_analyze_inproc", slow)
    speeches = [
        _speech(1, Camp.PRO, "正方长发言" * 20),
        _speech(2, Camp.PRO, "正方次发言" * 18, "pro-2"),
        _speech(3, Camp.CON, "反方长发言" * 20, "con-1"),
        _speech(4, Camp.CON, "反方次发言" * 18, "con-2"),
    ]
    text, marks = et.analyze_speeches_detailed(speeches, per_camp=2, total_budget=2.0)
    assert called["n"] <= 2, called  # 4 条候选但预算只够 2 条，第 3 条前提前收手
    assert text and marks


def test_judge_context_machine_analysis_position(assets_root):
    """机器分析段注入位置：原文摘录之后、要点清单之前；且仅逻辑评委获得。"""
    agents = load_agents(assets_root)
    topic = load_topics(assets_root)[0]
    ma = "【机器论证分析】（仅供参考）\n- [正方·pro-1] Slippery slope（high）：引用“x”"
    common = dict(topic=topic, digest_text=render_digest(Digest()),
                  excerpts=[], checklist="要点", instruction="评审")
    msgs_logic = build_judge_messages(spec=agents["judge-logic"], machine_analysis_text=ma, **common)
    msgs_content = build_judge_messages(spec=agents["judge-content"], machine_analysis_text="", **common)
    logic = msgs_logic[1]["content"]
    assert "【机器论证分析】" in logic
    assert logic.index("【原文摘录】") < logic.index("【机器论证分析】") < logic.index("【各环节要点清单】")
    assert "【机器论证分析】" not in msgs_content[1]["content"]  # 其他评委不注入


def test_mcp_stdio_client_protocol(tmp_path, monkeypatch):
    """MCP 模式：stdio JSON-RPC 客户端协议正确性（用本地假 server 验证，不依赖真 elenchus mcp）。"""
    import subprocess
    import sys
    import textwrap

    server_py = tmp_path / "fake_mcp_server.py"
    server_py.write_text(textwrap.dedent("""
        import json, sys
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            req = json.loads(line)
            if req.get("method") == "initialize":
                print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {"protocolVersion": "2024-11-05", "capabilities": {}}}), flush=True)
            elif req.get("method") == "notifications/initialized":
                pass
            elif req.get("method") == "tools/call" and req["params"]["name"] == "analyze":
                out = {"overall": "ok", "findings": [{"name": "Strawman", "quote": "q", "confidence": "low"}]}
                print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]}}), flush=True)
    """), encoding="utf-8")

    from debate.engine.tools import elenchus as et

    real_popen = subprocess.Popen  # patch 前留原引用（lambda 内再取会递归）

    def fake_popen(cmd, **kw):
        return real_popen([sys.executable, str(server_py)], **kw)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    client = et._McpStdioClient(timeout=5)
    try:
        result = client.call_analyze("测试论证", "fallacy")
        assert result["findings"][0]["name"] == "Strawman"
    finally:
        client.close()


def test_sensitivity_strict_passed_through(monkeypatch):
    """v2.6：sensitivity 配置透传到 analyze_one（parallel 与顺序两条路径都要带上）。"""
    from debate.engine.tools import elenchus as et

    seen: list[str | None] = []

    def fake_analyze_one(text, check="fallacy", timeout=45.0, mode="inproc", client=None,
                         sensitivity=None):
        seen.append(sensitivity)
        return {"findings": [{"name": "循环论证", "quote": text[:8],
                              "explanation": "用主张自证主张", "confidence": "low"}]}

    monkeypatch.setattr(et, "analyze_one", fake_analyze_one)
    speeches = [_speech(1, Camp.PRO, "正方立论" * 20), _speech(2, Camp.CON, "反方立论" * 20, "con-1")]

    et.analyze_speeches_detailed(speeches, per_camp=1, timeout=5.0, total_budget=10.0,
                                 parallel=1, sensitivity="strict")
    assert seen and all(s == "strict" for s in seen)


def test_collect_findings_cap_is_seven():
    """v2.6：strict 档最多报 7 条，单条归并上限从 4 放宽到 7。"""
    from debate.engine.tools.elenchus import _collect_findings

    result = {"findings": [{"name": f"瑕疵{i}", "quote": f"引用{i}", "explanation": "x",
                            "confidence": "low"} for i in range(9)]}
    lines, marks = [], []
    _collect_findings(_speech(1, Camp.PRO, "立论文本"), result, lines, marks)
    assert len(lines) == 7 and len(marks) == 7
