import type { Verdict } from "../api/types";
import { roleDisplay } from "./SpeechCard";

const RULE_CN: Record<string, string> = {
  majority: "评委投票多数",
  team_total: "团体总分",
  best_speaker: "最佳辩手归属",
  unresolved: "决胜规则用尽",
};

/** 胜方面板（Neumorphism）：凸起面板 + 强调色文字，无渐变无流光。 */
export function VerdictBanner({ verdict }: { verdict: Verdict }) {
  const best = verdict.best_speaker ? roleDisplay(verdict.best_speaker) : "无";
  const rule = RULE_CN[verdict.decisive_rule] ?? verdict.decisive_rule;
  const cls = verdict.winner === null ? "tie" : verdict.winner;
  const title =
    verdict.winner === null ? "本场平局，未分出胜方" : `本场胜方：${verdict.winner === "pro" ? "正方" : "反方"}`;
  return (
    <div className={`verdict-banner ${cls}`} data-testid="verdict-banner">
      <div className="verdict-title">{title}</div>
      <div className="verdict-sub">
        投票 {verdict.votes.pro} : {verdict.votes.con}（{rule}）｜最佳辩手：{best}
      </div>
    </div>
  );
}
