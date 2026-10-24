import { Table, Tag } from "antd";
import type { ScorecardSummary, Verdict } from "../api/types";
import { roleDisplay } from "./SpeechCard";

/** 六维雷达图：内联 SVG，无外部依赖（评分满分为 FULL 口径）。 */
const DIMS: { key: string; label: string; max: number }[] = [
  { key: "statement", label: "立论", max: 25 },
  { key: "rebuttal_cross", label: "驳论质询", max: 20 },
  { key: "attack", label: "攻辩", max: 15 },
  { key: "free", label: "自由辩", max: 20 },
  { key: "closing", label: "总结", max: 15 },
  { key: "conduct", label: "风度", max: 5 },
];

function polygonPoints(values: number[]): string {
  const cx = 110;
  const cy = 100;
  const radius = 80;
  return values
    .map((value, i) => {
      const angle = (Math.PI * 2 * i) / values.length - Math.PI / 2;
      const r = (Math.max(0, Math.min(1, value)) * radius);
      return `${(cx + r * Math.cos(angle)).toFixed(1)},${(cy + r * Math.sin(angle)).toFixed(1)}`;
    })
    .join(" ");
}

export function ScoreRadar({ pro, con }: { pro: number[]; con: number[] }) {
  const axes = DIMS.map((dim, i) => {
    const angle = (Math.PI * 2 * i) / DIMS.length - Math.PI / 2;
    const x = 110 + 80 * Math.cos(angle);
    const y = 100 + 80 * Math.sin(angle);
    return { ...dim, x, y, ring: [0.25, 0.5, 0.75, 1].map((f) => `${110 + 80 * f * Math.cos(angle)},${100 + 80 * f * Math.sin(angle)}`) };
  });
  return (
    <svg data-testid="score-radar" width="220" height="200" role="img">
      {[0.25, 0.5, 0.75, 1].map((f) => (
        <polygon
          key={f}
          points={DIMS.map((_, i) => {
            const angle = (Math.PI * 2 * i) / DIMS.length - Math.PI / 2;
            return `${110 + 80 * f * Math.cos(angle)},${100 + 80 * f * Math.sin(angle)}`;
          }).join(" ")}
          fill="none"
          stroke="#d9d9d9"
        />
      ))}
      {axes.map((a) => (
        <g key={a.key}>
          <line x1={110} y1={100} x2={a.x} y2={a.y} stroke="#d9d9d9" />
          <text x={a.x + (a.x - 110) * 0.18} y={a.y + (a.y - 100) * 0.18} fontSize="11" textAnchor="middle">
            {a.label}
          </text>
        </g>
      ))}
      <polygon points={polygonPoints(con)} fill="#dc262622" stroke="#dc2626" />
      <polygon points={polygonPoints(pro)} fill="#2563eb22" stroke="#2563eb" />
    </svg>
  );
}

export function ScoreBoard({ verdict }: { verdict: Verdict }) {
  const cards: ScorecardSummary[] = verdict.scorecards ?? [];
  const avg = (pick: (c: ScorecardSummary) => number) =>
    cards.length ? cards.map(pick).reduce((a, b) => a + b, 0) / cards.length : 0;
  const proAvg = avg((c) => c.pro_total);
  const conAvg = avg((c) => c.con_total);

  // 各维近似拆分：按满分比例把均分摊到六维（评分表未存分维明细时的展示口径）
  const totalMax = DIMS.reduce((acc, d) => acc + d.max, 0);
  const proValues = DIMS.map((d) => (proAvg / totalMax) * d.max);
  const conValues = DIMS.map((d) => (conAvg / totalMax) * d.max);

  return (
    <div data-testid="score-board">
      <Table
        size="small"
        pagination={false}
        rowKey="judge"
        dataSource={cards}
        columns={[
          { title: "评委", dataIndex: "judge", render: (v: string) => roleDisplay(v) },
          { title: "正方", dataIndex: "pro_total" },
          { title: "反方", dataIndex: "con_total" },
          { title: "投票", dataIndex: "vote", render: (v: string | null) => (v ? (v === "pro" ? "正方" : "反方") : "弃权") },
          { title: "最佳辩手", dataIndex: "best_speaker", render: (v: string | null) => (v ? roleDisplay(v) : "无") },
        ]}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 24, marginTop: 12 }}>
        <ScoreRadar pro={proValues} con={conValues} />
        <div>
          <p>
            <Tag color="blue">正方 {verdict.team_totals.pro}</Tag>
            <Tag color="red">反方 {verdict.team_totals.con}</Tag>
          </p>
          <p>
            投票 <strong>{verdict.votes.pro} : {verdict.votes.con}</strong>
            （决胜依据：{
              { majority: "评委投票多数", team_total: "团体总分", best_speaker: "最佳辩手归属", unresolved: "规则用尽" }[
                verdict.decisive_rule
              ] ?? verdict.decisive_rule
            }
            ）
          </p>
          <p>最佳辩手：{verdict.best_speaker ? roleDisplay(verdict.best_speaker) : "无"}</p>
        </div>
      </div>
    </div>
  );
}
