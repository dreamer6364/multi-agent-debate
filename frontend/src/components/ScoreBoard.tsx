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

const FOCUS_CN: Record<string, string> = { logic: "逻辑", content: "内容", expression: "表达" };

type Dim = { key: string; label: string; max: number };

/** 依实际存在的分维键裁剪维度；lite 赛制无 attack，rebuttal_cross 满分并入 35。 */
function resolveDims(cards: ScorecardSummary[]): Dim[] {
  const has = (key: string) =>
    cards.some(
      (c) => c.pro_scores?.[key] !== undefined || c.con_scores?.[key] !== undefined,
    );
  const present = DIMS.filter((d) => has(d.key));
  if (present.length === 0) return DIMS;
  return present.map((d) =>
    d.key === "rebuttal_cross" && !has("attack") ? { ...d, max: 35 } : d,
  );
}

function pick(scores: Record<string, number> | undefined, key: string): number | null {
  const v = scores?.[key];
  return typeof v === "number" ? v : null;
}

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

export function ScoreRadar({
  pro,
  con,
  dims = DIMS,
}: {
  pro: number[];
  con: number[];
  dims?: Dim[];
}) {
  const axes = dims.map((dim, i) => {
    const angle = (Math.PI * 2 * i) / dims.length - Math.PI / 2;
    const x = 110 + 80 * Math.cos(angle);
    const y = 100 + 80 * Math.sin(angle);
    return { ...dim, x, y };
  });
  return (
    <svg data-testid="score-radar" width="220" height="200" role="img">
      {[0.25, 0.5, 0.75, 1].map((f) => (
        <polygon
          key={f}
          points={dims
            .map((_, i) => {
              const angle = (Math.PI * 2 * i) / dims.length - Math.PI / 2;
              return `${110 + 80 * f * Math.cos(angle)},${100 + 80 * f * Math.sin(angle)}`;
            })
            .join(" ")}
          fill="none"
          stroke="#cdd3df"
        />
      ))}
      {axes.map((a) => (
        <g key={a.key}>
          <line x1={110} y1={100} x2={a.x} y2={a.y} stroke="#cdd3df" />
          <text x={a.x + (a.x - 110) * 0.18} y={a.y + (a.y - 100) * 0.18} fontSize="11" textAnchor="middle">
            {a.label}
          </text>
        </g>
      ))}
      <polygon points={polygonPoints(con)} fill="rgba(255,107,107,.14)" stroke="#ff6b6b" />
      <polygon points={polygonPoints(pro)} fill="rgba(109,93,252,.14)" stroke="#6d5dfc" />
    </svg>
  );
}

/** 单评委打分依据：分维明细（谁在哪一维赢了几分）+ 该评委的投票与理由入口。 */
function ScoreDetail({ card, dims }: { card: ScorecardSummary; dims: Dim[] }) {
  const hasDim = dims.some(
    (d) => pick(card.pro_scores, d.key) !== null || pick(card.con_scores, d.key) !== null,
  );
  return (
    <div className="sb-detail" data-testid="score-detail">
      <div className="sb-detail-head">
        <span>打分依据 · {roleDisplay(card.judge)}</span>
        <span className="sb-focus">侧重：{FOCUS_CN[card.focus] ?? card.focus}</span>
        <span className={`jr-vote ${card.vote ?? "abstain"}`}>
          {card.vote ? `投 ${card.vote === "pro" ? "正方" : "反方"}` : "弃权"}
        </span>
      </div>
      {hasDim ? (
        <table className="sb-dims">
          <thead>
            <tr>
              <th>评分维度</th>
              <th>正方</th>
              <th>反方</th>
              <th>本维占优</th>
            </tr>
          </thead>
          <tbody>
            {dims.map((d) => {
              const p = pick(card.pro_scores, d.key);
              const c = pick(card.con_scores, d.key);
              const diff = p !== null && c !== null ? p - c : null;
              return (
                <tr key={d.key}>
                  <td>
                    {d.label}
                    <span className="sb-dim-max">/{d.max}</span>
                  </td>
                  <td className={diff !== null && diff > 0 ? "sb-win pro" : ""}>{p ?? "—"}</td>
                  <td className={diff !== null && diff < 0 ? "sb-win con" : ""}>{c ?? "—"}</td>
                  <td>
                    {diff === null ? "—" : diff === 0 ? "持平" : diff > 0 ? `正方 +${diff}` : `反方 +${-diff}`}
                  </td>
                </tr>
              );
            })}
            <tr className="sb-total">
              <td>合计</td>
              <td>{card.pro_total}</td>
              <td>{card.con_total}</td>
              <td>
                {card.pro_total === card.con_total
                  ? "持平"
                  : card.pro_total > card.con_total
                    ? `正方 +${card.pro_total - card.con_total}`
                    : `反方 +${card.con_total - card.pro_total}`}
              </td>
            </tr>
          </tbody>
        </table>
      ) : (
        <p className="sb-no-dim">该场次未存分维明细，仅记录总分（早期场次）。</p>
      )}
      {(card.highlights || card.weaknesses) && (
        <p className="sb-reason-hint">投票理由（亮点 / 不足，引原话）见上方「评委理由卡」。</p>
      )}
    </div>
  );
}

export function ScoreBoard({ verdict }: { verdict: Verdict }) {
  const cards: ScorecardSummary[] = verdict.scorecards ?? [];
  const dims = resolveDims(cards);
  const avg = (pickTotal: (c: ScorecardSummary) => number) =>
    cards.length ? cards.map(pickTotal).reduce((a, b) => a + b, 0) / cards.length : 0;
  const proAvg = avg((c) => c.pro_total);
  const conAvg = avg((c) => c.con_total);

  // 分维均值：有明细就用真值，早期无明细的场次回落到"总分按满分比例摊派"的展示口径
  const totalMax = dims.reduce((acc, d) => acc + d.max, 0);
  const avgDim = (side: "pro" | "con", key: string) => {
    const vals = cards
      .map((c) => pick(side === "pro" ? c.pro_scores : c.con_scores, key))
      .filter((v): v is number => v !== null);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  };
  const proValues = dims.map((d) => {
    const v = avgDim("pro", d.key);
    return v === null ? (proAvg / totalMax) * d.max : v / d.max;
  });
  const conValues = dims.map((d) => {
    const v = avgDim("con", d.key);
    return v === null ? (conAvg / totalMax) * d.max : v / d.max;
  });

  return (
    <div data-testid="score-board">
      <Table
        size="small"
        pagination={false}
        rowKey="judge"
        dataSource={cards}
        expandable={{
          expandedRowRender: (card: ScorecardSummary) => <ScoreDetail card={card} dims={dims} />,
          rowExpandable: () => true,
          expandRowByClick: true,
        }}
        columns={[
          { title: "评委", dataIndex: "judge", render: (v: string) => roleDisplay(v) },
          {
            title: "评审侧重",
            dataIndex: "focus",
            render: (v: string) => <span className="sb-focus">{FOCUS_CN[v] ?? v}</span>,
          },
          { title: "正方", dataIndex: "pro_total" },
          { title: "反方", dataIndex: "con_total" },
          { title: "投票", dataIndex: "vote", render: (v: string | null) => (v ? (v === "pro" ? "正方" : "反方") : "弃权") },
          { title: "最佳辩手", dataIndex: "best_speaker", render: (v: string | null) => (v ? roleDisplay(v) : "无") },
        ]}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 24, marginTop: 12 }}>
        <ScoreRadar pro={proValues} con={conValues} dims={dims} />
        <div>
          <p>
            <Tag color="blue">正方 {verdict.team_totals.pro}</Tag>
            <Tag color="red">反方 {verdict.team_totals.con}</Tag>
          </p>
          <p>
            投票 <strong>{verdict.votes.pro} : {verdict.votes.con}</strong>
            （决胜依据：{
              { majority: "评委投票多数", team_total: "团体总分", best_speaker: "最佳辩手归属", final_judge: "评议长智能终裁", unresolved: "规则用尽" }[
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
