/** 动态计时条：凹槽轨道 + 强调色填充，配色随消耗率柔和推进（50% 琥珀、80% 珊瑚）。 */
export function TimerBar({ consumed, budget }: { consumed: number; budget: number }) {
  const pct = Math.min(100, Math.round((consumed / Math.max(1, budget)) * 100));
  const color = pct >= 80 ? "#ff6b6b" : pct >= 50 ? "#e0a23c" : "#6d5dfc";
  return (
    <div className="timer-wrap" data-testid="timer-bar">
      <div className="timer-track">
        <div className="timer-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="timer-text">
        {consumed} / {budget} 字（{pct}%）
      </span>
    </div>
  );
}
