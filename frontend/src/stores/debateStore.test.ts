import { beforeEach, describe, expect, it } from "vitest";
import { useDebateStore } from "./debateStore";

const D = (seq: number, delta: string, attempt = 0, speaker = "pro-1") =>
  ({ seq, delta, attempt, speaker, camp: "pro" as const });

describe("debateStore applyDelta/applySpeech 流式语义", () => {
  beforeEach(() => useDebateStore.getState().reset());

  it("FE-DELTA-01 delta 累积成打字机文本", () => {
    const s = useDebateStore.getState();
    s.applyDelta(D(1, ""));
    s.applyDelta(D(1, "对方"));
    s.applyDelta(D(1, "辩友"));
    const st = useDebateStore.getState().streaming!;
    expect(st.seq).toBe(1);
    expect(st.text).toBe("对方辩友");
    expect(st.speaker).toBe("pro-1");
  });

  it("FE-DELTA-02 attempt 变化重置累积（流式重试）", () => {
    const s = useDebateStore.getState();
    s.applyDelta(D(1, "半截文本"));
    s.applyDelta({ ...D(1, "", 1) }); // 重试重置信号
    s.applyDelta(D(1, "重来", 1));
    expect(useDebateStore.getState().streaming!.text).toBe("重来");
  });

  it("FE-DELTA-03 定稿替换打字卡；迟到 delta 丢弃", () => {
    const s = useDebateStore.getState();
    s.applyDelta(D(1, "流式片段"));
    s.applySpeech({ seq: 1, speaker: "pro-1", camp: "pro", text: "定稿全文", interrupted: false, side_notes: [] });
    expect(useDebateStore.getState().streaming).toBeNull();
    expect(useDebateStore.getState().speeches[0].text).toBe("定稿全文");
    s.applyDelta(D(1, "迟到的")); // 定稿后到达
    expect(useDebateStore.getState().streaming).toBeNull();
  });

  it("FE-DELTA-04 seq 轮转：新发言直接开新卡", () => {
    const s = useDebateStore.getState();
    s.applyDelta(D(1, "甲"));
    s.applyDelta(D(2, "", 0, "con-1"));
    s.applyDelta({ ...D(2, "乙", 0, "con-1"), camp: "con" as never });
    const st = useDebateStore.getState().streaming!;
    expect(st.seq).toBe(2);
    expect(st.text).toBe("乙");
  });
});

describe("debateStore 谬误标注挂载（v1.7 Elenchus）", () => {
  beforeEach(() => useDebateStore.getState().reset());

  const MARK = (seq: number, name = "滑坡谬误") =>
    ({ seq, camp: "pro" as const, speaker: "pro-1", name, quote: "原文", explanation: "解释", confidence: 0.9 });

  it("setFallacyMarks 按 seq 挂载；同名同引文去重", () => {
    useDebateStore.getState().setFallacyMarks([MARK(1)]);
    useDebateStore.getState().setFallacyMarks([MARK(1)]); // SSE + REST 补拉重叠
    const bySeq = useDebateStore.getState().fallaciesBySeq;
    expect(bySeq[1]).toHaveLength(1);
    useDebateStore.getState().setFallacyMarks([MARK(2, "虚假类比")]);
    expect(useDebateStore.getState().fallaciesBySeq[2]).toHaveLength(1);
  });

  it("ingestFallacies 终态补拉合并；无标注发言不影响既有数据", () => {
    useDebateStore.getState().setFallacyMarks([MARK(1)]);
    useDebateStore.getState().ingestFallacies([
      { seq: 1 },
      { seq: 3, fallacies: [MARK(3, "断章取义")] },
    ]);
    const bySeq = useDebateStore.getState().fallaciesBySeq;
    expect(bySeq[1]).toHaveLength(1);
    expect(bySeq[3]).toHaveLength(1);
  });

  it("applySpeech 携带 fallacies 时合并到对应 seq", () => {
    useDebateStore.getState().applySpeech({
      seq: 5, speaker: "con-2", camp: "con", text: "发言",
      interrupted: false, side_notes: [], fallacies: [MARK(5, "以偏概全")],
    });
    expect(useDebateStore.getState().fallaciesBySeq[5]).toHaveLength(1);
  });
});

describe("debateStore 打断实时修正（P1-1）", () => {
  beforeEach(() => useDebateStore.getState().reset());

  const ANSWER = { seq: 4, speaker: "pro-1", camp: "pro" as const, text: "完整回答的前半句。后半句是被截断的部分。", interrupted: false, side_notes: [], word_count: 24 };

  it("FE-INT-01 interrupt 就地截断已渲染发言并打上打断标记", () => {
    const s = useDebateStore.getState();
    s.applySpeech(ANSWER);
    s.applyInterrupt({
      by: "con-2", reason: "请正面回答", seq: 4,
      text: "完整回答的前半句。", side_notes: ["质询方打断：请正面回答"], word_count: 8,
    });
    const got = useDebateStore.getState().speeches[0];
    expect(got.text).toBe("完整回答的前半句。");
    expect(got.interrupted).toBe(true);
    expect(got.side_notes).toContain("质询方打断：请正面回答");
    expect(got.word_count).toBe(8);
  });

  it("FE-INT-02 无 seq 的旧版载荷与未知 seq 均为无操作", () => {
    const s = useDebateStore.getState();
    s.applySpeech(ANSWER);
    s.applyInterrupt({ by: "con-2", reason: "请正面回答" }); // 旧版：无 seq
    s.applyInterrupt({ by: "con-2", reason: "x", seq: 99, text: "不存在" });
    const got = useDebateStore.getState().speeches[0];
    expect(got.text).toBe(ANSWER.text);
    expect(got.interrupted).toBe(false);
  });

  it("FE-INT-03 打断落在流式打字卡上时同步截断", () => {
    const s = useDebateStore.getState();
    s.applyDelta({ seq: 4, delta: "前半句。后半句。", attempt: 0, speaker: "pro-1", camp: "pro" });
    s.applyInterrupt({ by: "con-2", reason: "请正面回答", seq: 4, text: "前半句。", word_count: 4 });
    const st = useDebateStore.getState().streaming!;
    expect(st.text).toBe("前半句。");
  });
});

describe("debateStore 复盘泳道总结（v2.7）", () => {
  beforeEach(() => useDebateStore.getState().reset());

  it("FE-SL-01 applySwimlane 整份替换；reset 清空", () => {
    const payload = { topic: "t", rows: [{ phase: 2, name: "开篇立论", pro: [], con: [], public: { receivers: "全场", timer: "剩余 30 秒提示", notes: [] } }] };
    useDebateStore.getState().applySwimlane(payload);
    expect(useDebateStore.getState().swimlane?.rows).toHaveLength(1);
    // 后一条发言推来新版本 → 整体覆盖
    const next = { topic: "t", rows: [{ phase: 2, name: "开篇立论", pro: [], con: [], public: { receivers: "全场", timer: "剩余 30 秒提示", notes: [] } }, { phase: 4, name: "驳论", pro: [], con: [], public: { receivers: "评委＋对方全员", timer: "剩余 30 秒提示", notes: [] } }] };
    useDebateStore.getState().applySwimlane(next);
    expect(useDebateStore.getState().swimlane?.rows).toHaveLength(2);
    useDebateStore.getState().reset();
    expect(useDebateStore.getState().swimlane).toBeNull();
  });
});
