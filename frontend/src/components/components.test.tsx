import { cleanup, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { LiveSpeech } from "../api/types";
import { PhaseTimeline } from "./PhaseTimeline";
import { SpeechCard, roleDisplay } from "./SpeechCard";

describe("SpeechCard", () => {
  const base: LiveSpeech = {
    seq: 1, speaker: "pro-1", camp: "pro", text: "正常发言", interrupted: false, side_notes: [],
  };

  it("FE-CMP-01 XSS 防护：HTML 注入按纯文本渲染", () => {
    const { container } = render(<SpeechCard speech={{
      ...base, text: '<img src=x onerror="alert(1)">你好',
    }} />);
    expect(container.querySelector("img")).toBeNull(); // 不产生真实元素
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">'); // 作为文本可见
    cleanup();
  });

  it("FE-CMP-02 旁注徽标与打断标记", () => {
    render(<SpeechCard speech={{
      ...base, interrupted: true,
      side_notes: ["计时员：还剩 30 秒", "违规：人身攻击"],
    }} />);
    expect(screen.getByText("已被打断")).toBeTruthy();
    expect(screen.getByText("计时员：还剩 30 秒")).toBeTruthy();
    expect(screen.getByText("违规：人身攻击")).toBeTruthy();
    cleanup();
  });

  it("角色显示映射", () => {
    expect(roleDisplay("pro-1")).toBe("正方·一辩");
    expect(roleDisplay("con-3")).toBe("反方·三辩");
    expect(roleDisplay("chair")).toBe("主席");
    expect(roleDisplay("judge-logic")).toBe("逻辑评委");
  });
});

describe("PhaseTimeline", () => {
  it("FE-CMP-03 lite 赛制渲染 6 个环节且当前环节高亮", () => {
    const { container } = render(<PhaseTimeline format="lite" currentPhase={7} />);
    const items = container.querySelectorAll(".ant-steps-item");
    expect(items.length).toBe(6);
    const active = container.querySelector(".ant-steps-item-active");
    expect(active?.textContent).toContain("自由辩论");
    cleanup();
  });

  it("full 赛制渲染 10 个环节", () => {
    const { container } = render(<PhaseTimeline format="full" currentPhase={1} />);
    expect(container.querySelectorAll(".ant-steps-item").length).toBe(10);
    cleanup();
  });
});
