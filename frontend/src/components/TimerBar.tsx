import { fmtDuration, WORDS_PER_MINUTE } from "./phases";

/**
 * 动态计时条：凹槽轨道 + 强调色填充，配色随消耗率柔和推进（50% 琥珀、80% 珊瑚、100% 红）。
 * 已用/剩余时间按语速 220 字/分钟从字数预算换算（与引擎计时口径一致）。
 */
export function TimerBar({
  consumed,
  budget,
  timeUp = false,
}: {
  consumed: number;
  budget: number;
  timeUp?: boolean;
}) {
  const pct = Math.min(100, Math.round((consumed / Math.max(1, budget)) * 100));
  const color = timeUp || pct >= 100 ? "#e5484d" : pct >= 80 ? "#ff6b6b" : pct >= 50 ? "#e0a23c" : "#6d5dfc";
  const elapsedSec = (consumed / WORDS_PER_MINUTE) * 60;
  const remainSec = Math.max(0, ((budget - consumed) / WORDS_PER_MINUTE) * 60);
  return (
    <div className="timer-wrap" data-testid="timer-bar">
      <div className="timer-track">
        <div className="timer-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className={`timer-text${timeUp || pct >= 100 ? " timer-over" : ""}`}>
        {consumed} / {budget} 字（{pct}%）｜已用 ≈{fmtDuration(elapsedSec)} · 剩余 ≈{fmtDuration(remainSec)}
      </span>
    </div>
  );
}
