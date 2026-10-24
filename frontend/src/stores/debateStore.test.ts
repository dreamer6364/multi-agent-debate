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
