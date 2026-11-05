import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, getSpeeches } from "./client";

const speech = (seq: number) => ({
  seq, phase: 7, phase_name: "自由辩论", speaker: "pro-1", camp: "pro",
  text: `第 ${seq} 条`, side_notes: [], interrupted: false, word_count: 5,
});

describe("getSpeeches（FE-2 按 has_more 拉全）", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("超过单页 500 条时按游标续拉，不静默截断", async () => {
    const first = { items: Array.from({ length: 500 }, (_, i) => speech(i + 1)), has_more: true };
    const second = { items: [speech(501)], has_more: false };
    const get = vi.spyOn(api, "get")
      .mockResolvedValueOnce({ data: first } as never)
      .mockResolvedValueOnce({ data: second } as never);

    const page = await getSpeeches(1, 0);
    expect(page.items).toHaveLength(501);
    expect(page.has_more).toBe(false);
    expect(get).toHaveBeenCalledTimes(2);
    expect(get.mock.calls[1][1]).toEqual({ params: { after_seq: 500, limit: 500 } });
  });

  it("单页即止，不重复请求", async () => {
    const get = vi.spyOn(api, "get")
      .mockResolvedValueOnce({ data: { items: [speech(1)], has_more: false } } as never);
    const page = await getSpeeches(7, 0);
    expect(page.items).toHaveLength(1);
    expect(get).toHaveBeenCalledTimes(1);
    expect(get.mock.calls[0][0]).toBe("/debates/7/speeches");
  });

  it("游标不推进（异常 has_more）时终止，不死循环", async () => {
    const stuck = { items: [speech(3)], has_more: true };
    const get = vi.spyOn(api, "get").mockResolvedValue({ data: stuck } as never);
    const page = await getSpeeches(1, 3);
    expect(get).toHaveBeenCalledTimes(1);
    expect(page.has_more).toBe(true);
  });
});
