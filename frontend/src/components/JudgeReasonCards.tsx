import type { ScorecardSummary } from "../api/types";
import { roleDisplay } from "./SpeechCard";

const CAMP_NAME = { pro: "正方", con: "反方" } as const;

function ReasonList({
  title,
  items,
  tone,
}: {
  title: string;
  items?: string[];
  tone: "pro" | "con" | "weak-pro" | "weak-con";
}) {
  if (!items || items.length === 0) return null;
  return (
    <div className="jr-block">
      <span className={`jr-label jr-${tone}`}>{title}</span>
      <ul className="jr-list">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

/**
 * 评委理由卡：为什么这样打分/投票——评审侧重点 + 投票依据 + 双方亮点/不足（引原话）。
 * 数据源：ScoreCard.highlights / weaknesses（引擎要求引原话）+ focus。
 */
export function JudgeReasonCards({ cards }: { cards: ScorecardSummary[] }) {
  const withReasons = cards.filter((c) => c.focus || c.highlights || c.weaknesses);
  if (withReasons.length === 0) {
    return <div className="neu-chip">本场未记录评委理由（评分卡输出被降级为弃权）。</div>;
  }
  return (
    <div className="jr-grid" data-testid="judge-reasons">
      {withReasons.map((card) => {
        const basis = card.vote ? card.highlights?.[card.vote]?.[0] : undefined;
        const hasReasons = Boolean(
          (card.highlights?.pro?.length ?? 0) + (card.highlights?.con?.length ?? 0) +
          (card.weaknesses?.pro?.length ?? 0) + (card.weaknesses?.con?.length ?? 0),
        );
        return (
          <div key={card.judge} className="jr-card neu-raised">
            <div className="jr-head">
              <span className="jr-judge">⚖️ {roleDisplay(card.judge)}</span>
              {card.focus && <span className="jr-focus">评审侧重：{card.focus}</span>}
              <span className={`jr-vote ${card.vote ?? "abstain"}`}>
                {card.vote ? `投 ${CAMP_NAME[card.vote]}` : "弃权"}
              </span>
              {card.rescued && <span className="jr-rescued" data-testid="jr-rescued">含系统兜底点评</span>}
            </div>
            {basis && (
              <p className="jr-basis">
                <span className={`jr-label jr-${card.vote}`}>投票依据</span>
                <span>{basis}</span>
              </p>
            )}
            <div className="jr-cols">
              <ReasonList title="正方亮点" items={card.highlights?.pro} tone="pro" />
              <ReasonList title="反方亮点" items={card.highlights?.con} tone="con" />
              <ReasonList title="正方不足" items={card.weaknesses?.pro} tone="weak-pro" />
              <ReasonList title="反方不足" items={card.weaknesses?.con} tone="weak-con" />
            </div>
            {!hasReasons && (
              <p className="jr-empty">该评委评分卡降级/弃权，未产出亮点与不足记录。</p>
            )}
            {card.best_speaker && (
              <div className="jr-best">最佳辩手：<strong>{roleDisplay(card.best_speaker)}</strong></div>
            )}
          </div>
        );
      })}
    </div>
  );
}
