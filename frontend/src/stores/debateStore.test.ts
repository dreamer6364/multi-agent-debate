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

describe("debateStore 动态图（v2.5）", () => {
  beforeEach(() => useDebateStore.getState().reset());

  const snap = (version: number) => ({
    version,
    round: version,
    state: {
      topic: "辩题", round: version, pro_agent: "pro-1", con_agent: "con-1",
      moderator: "chair", current_claim: "主张", phase: 2, phase_name: "开篇立论",
      converged: false, terminated: false, branch: "next_round", winner: null as null,
      node_count: version, edge_count: version - 1, flow_count: version,
    },
    views: { control: `flowchart TD\n  a${version}`, argument: `flowchart LR\n  b${version}` },
    active: `f${version}`, active_node: `n${version}`,
    added_nodes: [`n${version}`], added_edges: [], added_edge_kinds: [],
    converged: false, terminated: false, branch: "next_round",
  });

  it("FE-GRAPH-01 每轮图进入历史，当前图始终是最新版本", () => {
    const s = useDebateStore.getState();
    s.applyGraph(snap(1) as never);
    s.applyGraph(snap(2) as never);
    s.applyGraph(snap(3) as never);
    const st = useDebateStore.getState();
    expect(st.graphHistory.map((x) => x.version)).toEqual([1, 2, 3]);
    expect(st.graph?.version).toBe(3);
  });

  it("FE-GRAPH-02 同一版本重发（重连补发）只覆盖不重复追加", () => {
    const s = useDebateStore.getState();
    s.applyGraph(snap(1) as never);
    s.applyGraph(snap(2) as never);
    s.applyGraph(snap(2) as never);
    const st = useDebateStore.getState();
    expect(st.graphHistory).toHaveLength(2);
    expect(st.graph?.version).toBe(2);
  });

  it("FE-GRAPH-03 归档回看：hydrateGraphs 直接装载历史并定位最后一张", () => {
    useDebateStore.getState().hydrateGraphs([snap(5) as never, snap(3) as never, snap(4) as never]);
    const st = useDebateStore.getState();
    expect(st.graphHistory.map((x) => x.version)).toEqual([3, 4, 5]);
    expect(st.graph?.version).toBe(5);
  });

  it("FE-GRAPH-04 reset 清空图与历史（换场次不串图）", () => {
    const s = useDebateStore.getState();
    s.applyGraph(snap(1) as never);
    s.reset();
    const st = useDebateStore.getState();
    expect(st.graph).toBeNull();
    expect(st.graphHistory).toEqual([]);
  });
});
