import { Steps } from "antd";
import type { DebateFormat } from "../api/types";
import { phasesOf } from "./phases";

export function PhaseTimeline({
  format,
  currentPhase,
}: {
  format: DebateFormat;
  currentPhase: number | null;
}) {
  const phases = phasesOf(format);
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
