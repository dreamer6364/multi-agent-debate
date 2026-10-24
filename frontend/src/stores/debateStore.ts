import { create } from "zustand";
import type { LiveSpeech, SpeechDeltaEvent, TimerEvent, Verdict } from "../api/types";

export type DebateStatus = "pending" | "running" | "finished" | "failed" | "stopped" | null;

interface PhaseStartPayload {
  phase: number;
  name: string;
  actors: string[];
  budget_words: number;
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
  verdict: Verdict | null;
  connected: boolean;
  lastSeq: number;
  hydrate: (payload: { status: DebateStatus; speeches: LiveSpeech[] }) => void;
  applyDelta: (payload: SpeechDeltaEvent) => void;
  applySpeech: (speech: LiveSpeech) => void;
  setPhaseStart: (payload: PhaseStartPayload) => void;
  setTimer: (timer: TimerEvent) => void;
  setJudging: () => void;
  setVerdict: (verdict: Verdict) => void;
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
  verdict: null,
  connected: false,
  lastSeq: 0,
};

export const useDebateStore = create<DebateLiveState>((set) => ({
  ...INITIAL,
  hydrate: ({ status, speeches }) =>
    set((state) => ({
      status,
      speeches,
      lastSeq: speeches.length ? speeches[speeches.length - 1].seq : state.lastSeq,
    })),
  applySpeech: (speech) =>
    set((state) => {
      if (state.speeches.some((s) => s.seq === speech.seq)) return {}; // 重放 vs 实况重叠去重
      // 实况发言补当前环节戳（SSE 载荷无 phase 字段，动态计时条依赖它）
      const stamped: LiveSpeech = { ...speech, phase: speech.phase ?? state.phase ?? undefined };
      const speeches = [...state.speeches, stamped].sort((a, b) => a.seq - b.seq);
      // 定稿到达：清除对应打字中卡片
      const streaming = state.streaming && state.streaming.seq === speech.seq ? null : state.streaming;
      return { speeches, lastSeq: speeches[speeches.length - 1].seq, streaming };
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
    }),
  setTimer: (timer) => set({ timer }),
  setJudging: () => set({ judging: true }),
  setVerdict: (verdict) => set({ verdict }),
  setStatus: (status, error = null) => set({ status, error }),
  setConnected: (connected) => set({ connected }),
  reset: () => set({ ...INITIAL }),
}));
