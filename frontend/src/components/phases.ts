import type { DebateFormat } from "../api/types";

/** 环节元信息：编号 → 名称 / 标签 / 卡片样式种类（辩论专用视觉分层） */
export type PhaseKind = "opening" | "rebuttal" | "cross" | "free" | "judge" | "official";

export const FULL_PHASES = [
  { phase: 1, name: "主席开场" },
  { phase: 2, name: "开篇立论" },
  { phase: 3, name: "质询一辩" },
  { phase: 4, name: "驳论" },
  { phase: 5, name: "攻辩盘问" },
  { phase: 6, name: "攻辩小结" },
  { phase: 7, name: "自由辩论" },
  { phase: 8, name: "总结陈词" },
  { phase: 9, name: "评委点评" },
  { phase: 10, name: "宣判闭幕" },
  { phase: 11, name: "主席致辞" },
] as const;

// lite 赛制：精简的是"交锋环节"，主席开场与结束致辞属于仪式骨架，两种赛制都有（v2.2）
export const LITE_PHASES = FULL_PHASES.filter((p) => [1, 2, 4, 7, 8, 9, 10, 11].includes(p.phase));

export function phasesOf(format: DebateFormat) {
  return format === "full" ? FULL_PHASES : LITE_PHASES;
}

/** 环节短标签（发言卡上的【】标记） */
export const PHASE_TAG: Record<number, string> = {
  1: "主席",
  2: "立论",
  3: "质询",
  4: "驳论",
  5: "攻辩",
  6: "小结",
  7: "自由辩",
  8: "结辩",
  9: "点评",
  10: "宣判",
  11: "致辞",
};

/** 发言卡样式种类：立论/结辩厚重、质询/驳论中等、自由辩轻量、评委独立、其余官方 */
export function phaseKind(phase?: number, camp?: string): PhaseKind {
  if (phase === 2 || phase === 8) return "opening";
  if (phase === 4 || phase === 6) return "rebuttal";
  if (phase === 3 || phase === 5) return "cross";
  if (phase === 7) return "free";
  if (phase === 9) return "judge";
  if (camp === "neutral") return "official";
  return "rebuttal";
}

export function phaseNameOf(phase?: number): string {
  return FULL_PHASES.find((p) => p.phase === phase)?.name ?? "";
}

/** 计时口径：语速 220 字/分钟（引擎 config 默认值），字数预算换算估时 */
export const WORDS_PER_MINUTE = 220;

/** 秒 → "X 分 Y 秒" 紧凑展示 */
export function fmtDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} 秒`;
  return `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
}
