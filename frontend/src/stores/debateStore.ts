import { create } from "zustand";
import type { FallacyMark, GoldenQuote, LiveSpeech, SpeechDeltaEvent, SwimlanePayload, TimerEvent, Verdict } from "../api/types";

export type DebateStatus = "pending" | "running" | "finished" | "failed" | "stopped" | null;

interface PhaseStartPayload {
  phase: number;
  name: string;
  actors: string[];
  budget_words: number;
}

/** 评审进度（v1.9.2）：后端评审期每 10s 推一次，前端据此显示耗时并做卡死自检 */
export interface JudgingProgress {
  stage: string;
  completed: number;
  total: number;
  elapsed: number;
}

interface DebateLiveState {
  status: DebateStatus;
  error: string | null;
  phase: number | null;
  phaseName: string | null;
  actors: string[];
  budgetWords: number | null;
  speeches: LiveSpeech[];
  streaming: { seq: number; text: string; attempt: number; speaker: string; camp: LiveSpeech["camp"] } | null;
  timer: TimerEvent | null;
  judging: boolean;
  judgingProgress: JudgingProgress | null;
  verdict: Verdict | null;
  connected: boolean;
  lastSeq: number;
  /** v1.7 Elenchus 谬误标注，按发言 seq 挂载（SSE fallacy 事件 + REST 补拉合并） */
  fallaciesBySeq: Record<number, FallacyMark[]>;
  /** v2.7 复盘泳道总结（阶段 × 阵营泳道）：SSE swimlane 事件实时更新，归档由详情接口兜底 */
  swimlane: SwimlanePayload | null;
  /** v2.9 金句摘录：SSE golden_quote 增量推送，归档/刷新由详情接口 golden_quotes 兜底 */
  goldenQuotes: GoldenQuote[];
  hydrate: (payload: { status: DebateStatus; speeches: LiveSpeech[] }) => void;
  applyDelta: (payload: SpeechDeltaEvent) => void;
  applySpeech: (speech: LiveSpeech) => void;
  setPhaseStart: (payload: PhaseStartPayload) => void;
  setTimer: (timer: TimerEvent) => void;
  setJudging: () => void;
  setJudgingProgress: (payload: JudgingProgress) => void;
  setVerdict: (verdict: Verdict) => void;
  setFallacyMarks: (marks: FallacyMark[]) => void;
  ingestFallacies: (items: Array<{ seq: number; fallacies?: FallacyMark[] }>) => void;
  applySwimlane: (payload: SwimlanePayload) => void;
  applyGoldenQuotes: (quotes: GoldenQuote[]) => void;
  setStatus: (status: DebateStatus, error?: string | null) => void;
  setConnected: (connected: boolean) => void;
  reset: () => void;
}

const INITIAL = {
  status: null,
  error: null,
  phase: null,
  phaseName: null,
  actors: [] as string[],
  budgetWords: null,
  speeches: [] as LiveSpeech[],
  streaming: null,
  timer: null,
  judging: false,
  judgingProgress: null,
  verdict: null,
  connected: false,
  lastSeq: 0,
  fallaciesBySeq: {},
  swimlane: null,
  goldenQuotes: [],
};

/** 谬误标注去重合并：同名+同引文视为同一条（SSE 与 REST 补拉重叠场景） */
function mergeMarks(
  current: Record<number, FallacyMark[]>,
  incoming: Iterable<FallacyMark>,
): Record<number, FallacyMark[]> {
  const next = { ...current };
  for (const m of incoming) {
    const arr = next[m.seq] ?? [];
    if (!arr.some((x) => x.name === m.name && x.quote === m.quote)) {
      next[m.seq] = [...arr, m];
    }
  }
  return next;
}

export const useDebateStore = create<DebateLiveState>((set) => ({
  ...INITIAL,
  hydrate: ({ status, speeches }) =>
    set((state) => ({
      status,
      speeches,
      lastSeq: speeches.length ? speeches[speeches.length - 1].seq : state.lastSeq,
      fallaciesBySeq: mergeMarks(state.fallaciesBySeq, speeches.flatMap((s) => s.fallacies ?? [])),
    })),
  applySpeech: (speech) =>
    set((state) => {
      if (state.speeches.some((s) => s.seq === speech.seq)) {
        // 重放 vs 实况重叠去重；迟到补发的 fallacies 仍要合并
        if (speech.fallacies?.length) {
          return { fallaciesBySeq: mergeMarks(state.fallaciesBySeq, speech.fallacies) };
        }
        return {};
      }
      // 实况发言补当前环节戳（SSE 载荷无 phase 字段，动态计时条依赖它）
      const stamped: LiveSpeech = { ...speech, phase: speech.phase ?? state.phase ?? undefined };
      const speeches = [...state.speeches, stamped].sort((a, b) => a.seq - b.seq);
      // 定稿到达：清除对应打字中卡片
      const streaming = state.streaming && state.streaming.seq === speech.seq ? null : state.streaming;
      return {
        speeches,
        lastSeq: speeches[speeches.length - 1].seq,
        streaming,
        ...(speech.fallacies?.length
          ? { fallaciesBySeq: mergeMarks(state.fallaciesBySeq, speech.fallacies) }
          : {}),
      };
    }),
  applyDelta: (payload) =>
    set((state) => {
      if (state.speeches.some((s) => s.seq === payload.seq)) return {}; // 定稿已到，迟到 delta 丢弃
      const current = state.streaming;
      const sameStream =
        current !== null &&
        current.seq === payload.seq &&
        current.attempt === payload.attempt;
      if (!sameStream) {
        // 新发言（seq 变化）或流式重试（attempt 变化）：重置累积
        return {
          streaming: {
            seq: payload.seq,
            text: payload.delta, // 重置信号 delta="" → 空文本
            attempt: payload.attempt,
            speaker: payload.speaker,
            camp: payload.camp,
          },
        };
      }
      return { streaming: { ...current, text: current.text + payload.delta } };
    }),
  setPhaseStart: (payload) =>
    set({
      phase: payload.phase,
      phaseName: payload.name,
      actors: payload.actors,
      budgetWords: payload.budget_words,
      timer: null,
      judging: payload.name === "评委点评",
      judgingProgress: null,
    }),
  setTimer: (timer) => set({ timer }),
  setJudging: () => set({ judging: true }),
  setJudgingProgress: (payload) => set({ judging: true, judgingProgress: payload }),
  setVerdict: (verdict) => set({ verdict }),
  setFallacyMarks: (marks) => set((state) => ({ fallaciesBySeq: mergeMarks(state.fallaciesBySeq, marks) })),
  ingestFallacies: (items) =>
    set((state) => ({ fallaciesBySeq: mergeMarks(state.fallaciesBySeq, items.flatMap((i) => i.fallacies ?? [])) })),
  // v2.5：每轮一张图。同 version 重发（重连补发/重放）只覆盖不追加，避免历史里出现重复回合。
  // v2.7：整份泳道总结随发言推进整体替换（后端按实录全量现算，无版本合并问题）
  applySwimlane: (payload) => set({ swimlane: payload }),
  // v2.9 金句去重合并：quote 归一化文本为键（重连全量补发/详情兜底与增量事件重叠场景）
  applyGoldenQuotes: (quotes) =>
    set((state) => {
      const norm = (q: GoldenQuote) => q.quote.replace(/[\s，。、；：？！"'“”‘’（）()《》<>【】\[\]·…—\-,.:;?!]+/g, "").toLowerCase();
      const fresh = quotes.filter((q) => !state.goldenQuotes.some((x) => norm(x) === norm(q)));
      return fresh.length ? { goldenQuotes: [...state.goldenQuotes, ...fresh].sort((a, b) => a.seq - b.seq) } : {};
    }),
  setStatus: (status, error = null) => set({ status, error }),
  setConnected: (connected) => set({ connected }),
  reset: () => set({ ...INITIAL }),
}));
