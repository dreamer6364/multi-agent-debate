import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  closed = false;
  handlers: Record<string, Array<(ev: MessageEvent) => void>> = {};
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  addEventListener(name: string, fn: (ev: MessageEvent) => void): void {
    (this.handlers[name] ??= []).push(fn);
  }
  close(): void {
    this.closed = true;
  }
  emit(name: string, data: unknown): void {
    (this.handlers[name] ?? []).forEach((fn) => fn({ data: JSON.stringify(data) } as MessageEvent));
  }
  fail(): void {
    this.onerror?.();
  }
  open(): void {
    this.onopen?.();
  }
}

vi.mock("../api/client", () => ({
  getDebate: vi.fn(async () => ({ id: 1, format: "lite", status: "running", error: null })),
  getSpeeches: vi.fn(async () => ({
    items: [
      { seq: 1, speaker: "pro-1", camp: "pro", text: "历史发言", side_notes: [], interrupted: false },
    ],
    has_more: false,
  })),
}));

import { getSpeeches } from "../api/client";
import { useDebateStream } from "./useDebateStream";
import { useDebateStore } from "../stores/debateStore";

const HIST_SEED = vi.mocked(getSpeeches);

describe("useDebateStream", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.clearAllMocks();
    useDebateStore.getState().reset();
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
  });

  it("FE-STR-01 历史补齐后以最后 seq 锚点订阅，speech 按 seq 去重排序", async () => {
    renderHook(() => useDebateStream(1));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const es = MockEventSource.instances[0];
    expect(HIST_SEED).toHaveBeenCalledWith(1, 0);
    expect(es.url).toContain("/api/debates/1/stream?after_seq=1"); // 历史最后一条为锚点

    act(() => {
      es.emit("speech", { seq: 3, speaker: "con-1", camp: "con", text: "c", interrupted: false, side_notes: [] });
      es.emit("speech", { seq: 2, speaker: "pro-2", camp: "pro", text: "b", interrupted: false, side_notes: [] });
      es.emit("speech", { seq: 1, speaker: "pro-1", camp: "pro", text: "重复", interrupted: false, side_notes: [] });
    });
    expect(useDebateStore.getState().speeches.map((s) => s.seq)).toEqual([1, 2, 3]); // 去重 + 排序
    expect(useDebateStore.getState().lastSeq).toBe(3);
  });

  it("FE-STR-02 EventSource 连续失败 3 次降级为 5 秒轮询", async () => {
    const intervalSpy = vi.spyOn(window, "setInterval");
    renderHook(() => useDebateStream(1));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const es = MockEventSource.instances[0];
    const callsBefore = HIST_SEED.mock.calls.length;

    es.fail();
    es.fail();
    expect(intervalSpy.mock.calls.filter(([, ms]) => ms === 5000)).toHaveLength(0); // 未达阈值不降级
    es.fail();

    await waitFor(() => expect(intervalSpy.mock.calls.some(([, ms]) => ms === 5000)).toBe(true));
    const pollCall = intervalSpy.mock.calls.find(([, ms]) => ms === 5000)!;
    const [, ms] = pollCall as [unknown, number];
    expect(ms).toBe(5000);

    // 轮询周期手动触发两次，确认以 lastSeq 增量拉取
    const intervalFn = pollCall[0] as () => void;
    await act(async () => {
      intervalFn();
      await Promise.resolve();
      intervalFn();
      await Promise.resolve();
    });
    expect(HIST_SEED.mock.calls.length).toBeGreaterThanOrEqual(callsBefore + 2);
    expect(HIST_SEED).toHaveBeenLastCalledWith(1, 1); // 从 lastSeq=1 续拉
    es.close(); // 阻断原生重连
  });

  it("FE-STR-03 收到终态 status 后关闭连接", async () => {
    renderHook(() => useDebateStream(1));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const es = MockEventSource.instances[0];
    act(() => {
      es.emit("status", { status: "finished", error: null });
    });
    expect(useDebateStore.getState().status).toBe("finished");
    expect(es.closed).toBe(true);

    act(() => {
      es.emit("speech", { seq: 9, speaker: "pro-1", camp: "pro", text: "迟到", interrupted: false, side_notes: [] });
    });
    expect(useDebateStore.getState().speeches.map((s) => s.seq)).toEqual([1]); // 关闭后不再接收
  });

  it("卸载时关闭连接（清理函数）", async () => {
    const { unmount } = renderHook(() => useDebateStream(1));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const es = MockEventSource.instances[0];
    unmount();
    expect(es.closed).toBe(true);
  });
});
