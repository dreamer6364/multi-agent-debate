import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import type { LiveSpeech, ScorecardSummary } from "../api/types";
import { useDebateStore } from "../stores/debateStore";
import { FrameworkPanel } from "./FrameworkPanel";
import { JudgeReasonCards } from "./JudgeReasonCards";
import { PhaseTimeline } from "./PhaseTimeline";
import { RichText } from "./RichText";
import { WorkflowGraph } from "./WorkflowGraph";
import { ScoreBoard } from "./ScoreBoard";
import { SpeechCard, roleDisplay } from "./SpeechCard";

describe("SpeechCard", () => {
  const base: LiveSpeech = {
    seq: 1, speaker: "pro-1", camp: "pro", text: "正常发言", interrupted: false, side_notes: [],
  };

  beforeEach(() => useDebateStore.getState().reset());

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

  it("FE-CMP-04 环节分层：立论卡带【立论】标签，谬误标注渲染 ⚠️ 徽标", () => {
    useDebateStore.setState({
      fallaciesBySeq: {
        1: [{ seq: 1, camp: "pro", speaker: "pro-1", name: "滑坡谬误", quote: "原文片段", explanation: "解释", confidence: 0.88 }],
      },
    });
    render(<SpeechCard speech={{ ...base, phase: 2 }} />);
    expect(screen.getByText("【立论】")).toBeTruthy();
    const badge = screen.getByTestId("fallacy-badge");
    expect(badge.textContent).toContain("滑坡谬误");
    cleanup();
  });
});

describe("FrameworkPanel / JudgeReasonCards", () => {
  it("FE-CMP-05 立论框架：主持人开场发言可见 + 双方论点编号", () => {
    render(
      <FrameworkPanel
        detail={{
          id: 1, format: "full", status: "finished", cfg: {}, winner: "pro",
          best_speaker: null, team_totals: { pro: "10", con: "8" }, votes: { pro: 2, con: 1 },
          usage: null, error: null, started_at: null, finished_at: null, created_at: "",
          topic: {
            id: 1, title: "A / B", pro_stance: "正方立场", con_stance: "反方立场",
            pro_points: ["论点甲", "论点乙"], con_points: ["论点丙"],
          },
        }}
        chairSpeeches={[{
          seq: 1, speaker: "chair", camp: "neutral",
          text: "欢迎来到本场辩论", interrupted: false, side_notes: [],
        }]}
      />,
    );
    expect(screen.getByTestId("chair-keynote").textContent).toContain("欢迎来到本场辩论");
    const pro = screen.getByTestId("framework-pro");
    expect(pro.textContent).toContain("①");
    expect(pro.textContent).toContain("论点甲");
    cleanup();
  });

  it("FE-CMP-08 VS 横幅明确标注双方观点（不依赖辩题含「/」）", () => {
    render(
      <FrameworkPanel
        detail={{
          id: 3, format: "lite", status: "running", cfg: {}, winner: null,
          best_speaker: null, team_totals: { pro: "0", con: "0" }, votes: { pro: 0, con: 0 },
          usage: null, error: null, started_at: null, finished_at: null, created_at: "",
          topic: {
            id: 3, title: "选择未来还是现在",
            pro_stance: "选择现在", con_stance: "选择未来",
            pro_points: [], con_points: [],
          },
        }}
        chairSpeeches={[]}
      />,
    );
    const banner = screen.getByTestId("vs-banner");
    // 双方观点各归其位，格式「正方：观点 / 反方：观点」
    expect(banner.textContent).toContain("正方");
    expect(banner.textContent).toContain("选择现在");
    expect(banner.textContent).toContain("反方");
    expect(banner.textContent).toContain("选择未来");
    cleanup();
  });

  it("FE-CMP-09 旧版垃圾立场（支持：整句辩题）回落到用户填写的要点首行", () => {
    render(
      <FrameworkPanel
        detail={{
          id: 4, format: "lite", status: "running", cfg: {}, winner: null,
          best_speaker: null, team_totals: { pro: "0", con: "0" }, votes: { pro: 0, con: 0 },
          usage: null, error: null, started_at: null, finished_at: null, created_at: "",
          topic: {
            id: 4, title: "选择爱你的人还是你爱的人",
            pro_stance: "支持：选择爱你的人还是你爱的人", con_stance: "",
            pro_points: ["选择爱你的人", "情感连接更真实"], con_points: [],
          },
        }}
        chairSpeeches={[]}
      />,
    );
    const banner = screen.getByTestId("vs-banner");
    expect(banner.textContent).toContain("选择爱你的人"); // 回落到要点首行，而非整句辩题
    expect(banner.textContent).not.toContain("支持：选择爱你的人还是你爱的人");
    const proPanel = screen.getByTestId("framework-pro");
    expect(proPanel.textContent).toContain("情感连接更真实"); // 被消费的首行不再重复出现在论点里
    cleanup();
  });

  it("FE-CMP-07 立论框架：引题演讲在开赛前、报幕在后（中立主席承担）", () => {
    render(
      <FrameworkPanel
        detail={{
          id: 2, format: "full", status: "finished", cfg: {}, winner: null,
          best_speaker: null, team_totals: { pro: "0", con: "0" }, votes: { pro: 0, con: 0 },
          usage: null, error: null, started_at: null, finished_at: null, created_at: "",
          topic: { id: 2, title: "A / B", pro_points: ["论点甲"], con_points: [] },
        }}
        chairSpeeches={[
          { seq: 1, speaker: "chair", camp: "neutral", phase: 1, phase_name: "主席开场",
            text: "今晚这道辩题，说到底是一个关于代价的问题。", interrupted: false, side_notes: [] },
          { seq: 2, speaker: "chair", camp: "neutral", phase: 1, phase_name: "主席开场",
            text: "欢迎来到本场辩论赛。", interrupted: false, side_notes: [] },
        ]}
      />,
    );
    const keynote = screen.getByTestId("chair-keynote");
    expect(keynote.textContent).toContain("主席 · 开篇演讲");
    expect(keynote.textContent).toContain("开赛前导览");
    expect(keynote.textContent).toContain("开场报幕");
    expect(keynote.textContent).toContain("引题演讲");
    expect(keynote.textContent).toContain("今晚这道辩题，说到底是一个关于代价的问题");
    expect(keynote.textContent).toContain("中立");
    // 辩手不再在立论框架中发言
    expect(screen.queryByTestId("opening-pro")).toBeNull();
    cleanup();
  });

  it("FE-CMP-08 评分表：展开评委行显示分维打分依据，雷达图按真实维度", () => {
    const { container } = render(
      <ScoreBoard
        verdict={{
          votes: { pro: 2, con: 1 }, team_totals: { pro: 78, con: 72 },
          winner: "pro", best_speaker: "pro-2", decisive_rule: "majority",
          scorecards: [{
            judge: "judge-logic", focus: "logic", vote: "pro", best_speaker: "pro-2",
            abstained: false, pro_total: 78, con_total: 72,
            pro_scores: { statement: 21, rebuttal_cross: 17, free: 16, closing: 12, conduct: 4 },
            con_scores: { statement: 19, rebuttal_cross: 15, free: 15, closing: 12, conduct: 5 },
            highlights: { pro: ["“我方数据来自三年追踪”"], con: [],
            },
            weaknesses: { pro: [], con: ["“样本只覆盖城市”"] },
          }],
        }}
      />,
    );
    const icon = container.querySelector(".ant-table-row-expand-icon") as HTMLElement;
    fireEvent.click(icon);
    const detail = screen.getByTestId("score-detail");
    expect(detail.textContent).toContain("打分依据");
    expect(detail.textContent).toContain("立论");
    expect(detail.textContent).toContain("正方 +2"); // statement 21 vs 19
    expect(detail.textContent).toContain("合计");
    // lite 口径无 attack 维度时雷达图不应出现"攻辩"轴
    expect(screen.getByTestId("score-radar").textContent).not.toContain("攻辩");
    cleanup();
  });

  it("FE-CMP-06 评委理由卡：亮点/不足与投票理由渲染", () => {
    const cards: ScorecardSummary[] = [{
      judge: "judge-logic", focus: "论证链条", vote: "pro", best_speaker: "pro-2",
      abstained: false, pro_total: 80, con_total: 70,
      highlights: { pro: ["指出对方类比不当"] }, weaknesses: { con: ["数据来源未说明"] },
    }];
    render(<JudgeReasonCards cards={cards} />);
    const panel = screen.getByTestId("judge-reasons");
    expect(panel.textContent).toContain("论证链条");
    expect(panel.textContent).toContain("正方亮点");
    expect(panel.textContent).toContain("指出对方类比不当");
    expect(panel.textContent).toContain("数据来源未说明");
    cleanup();
  });
});

describe("PhaseTimeline", () => {
  it("FE-CMP-03 lite 赛制渲染 8 个环节（含主席开场与结束致辞）且当前环节高亮", () => {
    const { container } = render(<PhaseTimeline format="lite" currentPhase={7} />);
    const items = container.querySelectorAll(".ant-steps-item");
    expect(items.length).toBe(8);
    expect(container.textContent).toContain("主席开场");
    expect(container.textContent).toContain("主席致辞");
    const active = container.querySelector(".ant-steps-item-active");
    expect(active?.textContent).toContain("自由辩论");
    cleanup();
  });

  it("full 赛制渲染 11 个环节", () => {
    const { container } = render(<PhaseTimeline format="full" currentPhase={1} />);
    expect(container.querySelectorAll(".ant-steps-item").length).toBe(11);
    cleanup();
  });
});

describe("RichText", () => {
  it("成对 ** 渲染为粗体，落单星号剔除（发言不再出现裸 *）", () => {
    const { container } = render(
      <RichText text={"**定义与判准**：我方主张 A。\n3*4 与 **重点** 保留"} />,
    );
    expect(container.querySelectorAll("strong").length).toBe(2);
    expect(container.textContent).toContain("定义与判准：我方主张 A。");
    expect(container.textContent).not.toContain("*");
    expect(container.querySelectorAll("br").length).toBe(1); // 换行保留
  });

  it("流式半截文本（加粗未闭合）也不会把星号漏到屏幕上", () => {
    const { container } = render(<RichText text={"**定义与判"} />);
    expect(container.textContent).toBe("定义与判");
  });
});

describe("WorkflowGraph", () => {
  const say = (seq: number, phase: number, camp: LiveSpeech["camp"], speaker: string): LiveSpeech => ({
    seq, speaker, camp, text: "发言", interrupted: false, side_notes: [], phase, word_count: 100,
  });

  it("FE-CMP-10 随直播高亮当前环节，并统计各侧发言条数", () => {
    const { container } = render(
      <WorkflowGraph
        format="full"
        currentPhase={3}
        status="running"
        speeches={[say(1, 2, "pro", "pro-1"), say(2, 2, "con", "con-1"), say(3, 3, "con", "con-2")]}
        streamingCamp="pro"
        streamingSpeaker="pro-2"
      />,
    );
    // full 赛制 11 个环节全部渲染
    expect(container.querySelectorAll("[data-testid^='wf-phase-']").length).toBe(11);
    // 已完成的立论为 done，当前质询为 active，后续环节 pending
    expect(container.querySelector("[data-testid='wf-phase-2']")?.getAttribute("data-state")).toBe("done");
    expect(container.querySelector("[data-testid='wf-phase-3']")?.getAttribute("data-state")).toBe("active");
    expect(container.querySelector("[data-testid='wf-phase-7']")?.getAttribute("data-state")).toBe("pending");
    // 立论两侧各 1 条，质询正方席位显示「发言中」
    const proSlot = container.querySelector("[data-testid='wf-slot-2-pro']");
    expect(proSlot?.textContent).toContain("1 条");
    expect(container.querySelector("[data-testid='wf-slot-3-pro']")?.textContent).toContain("发言中");
    // 状态条给出步骤进度
    expect(container.querySelector("[data-testid='wf-status']")?.textContent).toContain("第 3/11 步");
    cleanup();
  });

  it("FE-CMP-11 质询/攻辩行画出「谁向谁」的双向箭头，lite 只渲染 8 环节", () => {
    const lite = render(
      <WorkflowGraph format="lite" currentPhase={2} status="running" speeches={[]} />,
    );
    expect(lite.container.querySelectorAll("[data-testid^='wf-phase-']").length).toBe(8);
    // lite 无质询/攻辩，不应出现交叉箭头标签
    expect(lite.container.textContent).not.toContain("质询");
    cleanup();

    const full = render(
      <WorkflowGraph format="full" currentPhase={3} status="running" speeches={[]} />,
    );
    const qc = full.container.querySelector("[data-testid='wf-phase-3']");
    expect(qc?.textContent).toContain("质询");
    expect(qc?.querySelectorAll("line[marker-start]").length).toBe(1); // 双向箭头
    cleanup();
  });

  it("FE-CMP-12 已结束场次不再有高亮环节；点击席位回传环节号", () => {
    const seen: number[] = [];
    const { container } = render(
      <WorkflowGraph
        format="lite"
        currentPhase={11}
        status="finished"
        speeches={[say(1, 2, "pro", "pro-1"), say(2, 11, "neutral", "chair")]}
        onSelectPhase={(p) => seen.push(p)}
      />,
    );
    expect(container.querySelectorAll("[data-state='active']").length).toBe(0);
    fireEvent.click(container.querySelector("[data-testid='wf-slot-2-pro']")!);
    expect(seen).toEqual([2]);
    cleanup();
  });
});
