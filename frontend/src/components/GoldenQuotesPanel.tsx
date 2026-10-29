import { Tag } from "antd";
import type { GoldenQuote } from "../api/types";

/**
 * 金句摘录面板（v2.9）：随辩论并行提取的有价值句子。
 *
 * 数据纪律与流式质检一致——后端逐条发言异步摘录，quote 必须是发言原句逐字摘录
 * （LLM 提示词硬约束 + 本地校验），store 层按归一化文本去重；本组件只负责排版。
 */

const KIND_META: Record<string, { className: string }> = {
  逻辑锋利: { className: "gq-kind-sharp" },
  修辞出彩: { className: "gq-kind-rhetoric" },
  洞见深刻: { className: "gq-kind-insight" },
};

const CAMP_META = {
  pro: { label: "正方", className: "gq-camp-pro" },
  con: { label: "反方", className: "gq-camp-con" },
} as const;

export function GoldenQuotesPanel({ quotes }: { quotes: GoldenQuote[] }) {
  if (!quotes.length) {
    return (
      <div className="gq-empty" data-testid="golden-empty">
        <p className="gq-empty-title">✨ 金句随辩论逐步摘录</p>
        <p className="gq-empty-sub">
          每条辩手发言落定后，金句评审会异步从中摘出真正有价值的句子——逻辑锋利、修辞出彩或洞见深刻，
          摘录原句逐字上屏，宁缺毋滥。
        </p>
      </div>
    );
  }

  return (
    <div className="gq" data-testid="golden-panel">
      <div className="gq-list">
        {quotes.map((q, i) => {
          const kind = KIND_META[q.kind] ?? KIND_META["洞见深刻"];
          const camp = CAMP_META[q.camp as keyof typeof CAMP_META] ?? null;
          return (
            <figure className="gq-card" key={`${q.seq}-${i}`} data-testid="golden-quote">
              <blockquote className="gq-text">
                <span className={`gq-kind ${kind.className}`}>{q.kind}</span>
                「{q.quote}」
              </blockquote>
              <figcaption className="gq-meta">
                <span className={`gq-speaker ${camp?.className ?? ""}`}>
                  {camp ? `${camp.label} · ` : ""}
                  {q.speaker}
                </span>
                <span className="gq-phase">{q.phase_name}</span>
                {q.comment && <span className="gq-comment" title={q.comment}>{q.comment}</span>}
              </figcaption>
            </figure>
          );
        })}
      </div>
      <p className="gq-note">
        金句由金句评审随辩论并行摘录（与辩论生成互不阻塞）：quote 一律为发言原句逐字摘录，按发言先后排列；
        全场总量封顶，宁缺毋滥。
      </p>
    </div>
  );
}

export function GoldenCountTag({ count }: { count: number }) {
  if (!count) return null;
  return <Tag className="gq-count-tag">✨ {count}</Tag>;
}
