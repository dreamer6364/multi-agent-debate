import { Empty } from "antd";
import type { FallacyMark } from "../api/types";
import { useDebateStore } from "../stores/debateStore";
import { roleDisplay } from "./SpeechCard";
import { phaseNameOf } from "./phases";

/**
 * 质检报告（Elenchus 谬误汇总）：本场所有机器标注按发言顺序排列，
 * 每条含谬误类型 / 原文片段 / 解释 / 置信度，并可回链到辩论记录中的对应发言。
 */
export function FallacyReport({ speeches }: { speeches: Array<{ seq: number; phase?: number; speaker: string }> }) {
  const fallaciesBySeq = useDebateStore((s) => s.fallaciesBySeq);
  const rows: Array<{ mark: FallacyMark; seq: number; phase?: number; speaker: string }> = [];
  for (const s of speeches) {
    for (const mark of fallaciesBySeq[s.seq] ?? []) {
      rows.push({ mark, seq: s.seq, phase: s.phase, speaker: s.speaker });
    }
  }
  rows.sort((a, b) => a.seq - b.seq);

  if (rows.length === 0) {
    return (
      <div className="neu-raised qc-empty" data-testid="fallacy-report">
        <Empty description="暂无谬误标注（Elenchus 未启用、未检出，或比赛尚未评审完成）" />
      </div>
    );
  }

  return (
    <div data-testid="fallacy-report">
      <div className="qc-summary neu-chip info">
        本场共检出 {rows.length} 条论证瑕疵（Elenchus 机器标注，仅供评委参考，不等于评委结论）
      </div>
      {rows.map(({ mark, seq, phase }) => (
        <div key={`${seq}-${mark.name}-${mark.quote.slice(0, 12)}`} className="qc-row neu-raised">
          <div className="qc-row-head">
            <span className={`qc-camp ${mark.camp}`}>{roleDisplay(mark.speaker)}</span>
            <span className="qc-phase">{phaseNameOf(phase) || `第 ${seq} 条发言`}</span>
            <span className="qc-seq">#{seq}</span>
          </div>
          <div className="qc-name">⚠️ {mark.name}</div>
          {mark.quote && <blockquote className="qc-quote">“{mark.quote}”</blockquote>}
          <p className="qc-explain">{mark.explanation}</p>
          <div className="qc-foot">
            {typeof mark.confidence === "number" && <span>置信度 {Math.round(mark.confidence * 100)}%</span>}
            <span className="qc-link-hint">见「辩论记录」第 {seq} 条发言 ⚠️ 标记</span>
          </div>
        </div>
      ))}
    </div>
  );
}
