import { Steps } from "antd";
import type { DebateFormat } from "../api/types";

const FULL_PHASES = [
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
];

// lite 赛制仅五环节 + 宣判（SPEC §4.1）
const LITE_PHASES = FULL_PHASES.filter((p) => [2, 4, 7, 8, 9, 10].includes(p.phase));

export function PhaseTimeline({
  format,
  currentPhase,
}: {
  format: DebateFormat;
  currentPhase: number | null;
}) {
  const phases = format === "full" ? FULL_PHASES : LITE_PHASES;
  const currentIndex = currentPhase === null ? -1 : phases.findIndex((p) => p.phase === currentPhase);
  return (
    <Steps
      size="small"
      data-testid="phase-timeline"
      current={currentIndex}
      items={phases.map((p) => ({ title: p.name, key: p.phase }))}
    />
  );
}
