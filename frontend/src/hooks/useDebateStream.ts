import { useEffect } from "react";
import { getDebate, getSpeeches } from "../api/client";
import type { DebateStatus, LiveSpeech, PhaseStartEvent, SpeechDeltaEvent, TimerEvent, Verdict } from "../api/types";
import { useDebateStore } from "../stores/debateStore";

/** 轮询兜底间隔（SPEC §9.2：SSE 连续失败 3 次后降级为 5 秒轮询） */
export const SSE_POLL_INTERVAL_MS = 5000;
const MAX_ES_FAILURES = 3;
const TERMINAL: ReadonlySet<string> = new Set(["finished", "failed", "stopped"]);

/**
 * 辩场直播 Hook（TEST_PLAN §9.2）：
 * ① REST 补齐历史（刷新场景） → ② EventSource 订阅增量（?after_seq=最后 seq）
 * → ③ 事件分发到 zustand store → ④ 断线由浏览器自动重连，连续失败 3 次降级轮询
 * → ⑤ 收到终态 status 后关闭连接。
 *
 * 竞态纪律：取消标志用 effect 闭包变量（非共享 ref）——React StrictMode 双执行时
 * 第一个 effect 的 cleanup 必须永久作废其 async 延续，否则会出现双 EventSource 订阅，
 * speech_delta 无去重保护会被重复累加（v2.1 实测踩坑）。
 */
export function useDebateStream(debateId: number | string | undefined): void {
  const reset = useDebateStore((s) => s.reset);

  useEffect(() => {
    if (debateId === undefined) return;
    let cancelled = false; // 本 effect 实例专属，cleanup 后永不复位
    let es: EventSource | null = null;
    let pollTimer: number | null = null;
    let failures = 0;

    useDebateStore.getState().reset();

    const stopAll = () => {
      cancelled = true;
      es?.close();
      es = null;
      if (pollTimer !== null) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const startPolling = () => {
      if (pollTimer !== null || cancelled) return;
      pollTimer = window.setInterval(() => {
        void (async () => {
          if (cancelled) return;
          try {
            const detail = await getDebate(debateId);
            if (cancelled) return;
            useDebateStore.getState().setStatus(detail.status as DebateStatus, detail.error);
            if (TERMINAL.has(detail.status)) {
              stopAll();
              return;
            }
            const page = await getSpeeches(debateId, useDebateStore.getState().lastSeq);
            page.items.forEach((s) => useDebateStore.getState().applySpeech(s));
          } catch {
            /* 下个周期重试 */
          }
        })();
      }, SSE_POLL_INTERVAL_MS);
    };

    void (async () => {
      // ① 详情 + 历史补齐
      try {
        const detail = await getDebate(debateId);
        const page = await getSpeeches(debateId, 0);
        if (cancelled) return; // cleanup 已发生：丢弃本轮结果（StrictMode 首挂载）
        useDebateStore.getState().setStatus(detail.status as DebateStatus, detail.error);
        page.items.forEach((s) => useDebateStore.getState().applySpeech(s));
      } catch {
        /* 详情失败不阻塞直播订阅 */
      }
      if (cancelled) return;

      // ② SSE 订阅
      const lastSeq = useDebateStore.getState().lastSeq;
      es = new EventSource(`/api/debates/${debateId}/stream?after_seq=${lastSeq}`);

      const bind = <T,>(name: string, handler: (data: T) => void) =>
        es!.addEventListener(name, (ev: MessageEvent) => {
          if (!cancelled) handler(JSON.parse(ev.data) as T);
        });

      bind<LiveSpeech>("speech", (d) => useDebateStore.getState().applySpeech(d));
      bind<SpeechDeltaEvent>("speech_delta", (d) => useDebateStore.getState().applyDelta(d));
      bind<PhaseStartEvent>("phase_start", (d) => useDebateStore.getState().setPhaseStart(d));
      bind<TimerEvent>("timer", (d) => useDebateStore.getState().setTimer(d));
      bind("judging", () => useDebateStore.getState().setJudging());
      bind<Verdict>("verdict", (d) => useDebateStore.getState().setVerdict(d));
      bind("error", () => undefined); // error 事件仅提示性；终态由 status 承载
      bind<{ status: DebateStatus; error: string | null }>("status", (d) => {
        useDebateStore.getState().setStatus(d.status, d.error);
        if (TERMINAL.has(d.status)) stopAll();
      });

      es.onopen = () => {
        if (cancelled) return;
        failures = 0;
        useDebateStore.getState().setConnected(true);
      };
      es.onerror = () => {
        if (cancelled) return;
        useDebateStore.getState().setConnected(false);
        failures += 1;
        if (failures >= MAX_ES_FAILURES) {
          es?.close();
          es = null;
          startPolling(); // ③ 降级轮询
        }
      };
    })();

    return stopAll;
  }, [debateId, reset]);
}
