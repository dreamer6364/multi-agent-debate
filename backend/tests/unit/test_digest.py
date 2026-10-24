"""战局摘要单测（TEST_PLAN UT-DIG-01~03，SPEC §4.5）。"""
from debate.engine.schema import Camp, Speech
from debate.engine.sim.digest import Digest, extract_excerpts, merge_digest, render_digest


def mk(seq: int, phase: int, camp: Camp, text: str, speaker: str = "pro-1") -> Speech:
    return Speech(
        seq=seq, phase=phase, phase_name="测试", speaker=speaker, camp=camp,
        text=text, word_count=len(text),
    )


LONG = "正方一辩的完整立论发言，长度足够被摘录。" * 2  # ≥ min_chars(40)


def test_extract_excerpts_caps_per_phase_and_filters():
    speeches = [
        mk(1, 2, Camp.PRO, LONG),
        mk(2, 2, Camp.CON, LONG, speaker="con-1"),
        mk(3, 2, Camp.PRO, LONG),
        mk(4, 2, Camp.CON, LONG, speaker="con-1"),
        mk(5, 2, Camp.PRO, LONG),  # 环节内第 5 条：被 per_phase 上限排除
        mk(6, 2, Camp.NEUTRAL, LONG, speaker="chair"),  # 中立角色不入池
        mk(7, 2, Camp.PRO, "太短"),  # 低于 min_chars
        mk(8, 4, Camp.PRO, LONG),
    ]
    excerpts = extract_excerpts(speeches, per_phase=4)
    assert len([e for e in excerpts if e.phase == 2]) == 4
    assert all(e.speaker != "chair" for e in excerpts)
    assert any(e.phase == 4 for e in excerpts)


def test_merge_appends_and_protects_clashes_and_fallacies():
    prev = Digest(clashes="第一环节交锋：定义之争。", fallacies="对方以偏概全。")
    compress_calls: list[str] = []

    def compress(label: str, text: str) -> str:
        compress_calls.append(label)
        return text[: len(text) // 2]

    merged = merge_digest(
        prev,
        {"definition_judge": "双方定义分歧加深。", "arguments": "正方补第二论点。", "situation": "局势胶着。"},
        excerpts=[],
        max_chars=80,
        compress=compress,
    )
    assert "第一环节交锋" in merged.clashes and "定义之争" in merged.clashes
    assert "对方以偏概全" in merged.fallacies
    # 预算内无需压缩；即便触发压缩，交锋点/谬误指控也排在最后兜底
    assert all(label not in ("交锋点", "谬误指控") for label in compress_calls)


def test_render_contains_columns_and_excerpt_pool():
    digest = Digest(definition_judge="定义已立", clashes="有交锋", fallacies="有归谬")
    text = render_digest(digest)
    assert "【定义与判准】" in text
    assert "【交锋点】" in text
    assert "【原文摘录】" in text
