"""FakeLLM 替身自测（TEST_PLAN §2.1 基建可用性）。"""
import json

import pytest

from tests.fakes.llm import FakeLLM, FakeLLMError, ScriptExhausted

SCRIPT = {"meta": {"format": "lite"}, "turns": [{"speaker": "pro-1", "text": "甲"}, {"speaker": "con-1", "text": "乙"}]}


def test_scripted_pops_in_order(tmp_path):
    path = tmp_path / "script.json"
    path.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    llm = FakeLLM.scripted(path)
    assert llm.complete([]) == "甲"
    assert llm.complete([]) == "乙"
    with pytest.raises(ScriptExhausted):
        llm.complete([])


def test_echo_repeats_placeholder():
    llm = FakeLLM(mode="echo")
    out = llm.complete([{"role": "user", "content": "请发言 {repeat:10}"}])
    assert len(out) == 10


def test_fault_modes():
    with pytest.raises(FakeLLMError):
        FakeLLM(mode="fault", error_kind="http_500").complete([])
    assert "不是" in FakeLLM(mode="fault", error_kind="malformed_json").complete([])
    assert len(FakeLLM(mode="fault", error_kind="oversized").complete([])) == 3000
    assert "废物" in FakeLLM(mode="fault", error_kind="abuse").complete([])
    assert FakeLLM(mode="fault", error_kind="interrupt_spam").complete([]).count("[打断]") == 3
    assert "作为AI" in FakeLLM(mode="fault", error_kind="meta").complete([])
