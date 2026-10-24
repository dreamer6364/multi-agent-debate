/** 动态计时条：按当前环节已消耗字数 / 预算渲染，配色随消耗率渐变（绿→琥珀→红），告急时脉冲。 */
export function TimerBar({ consumed, budget }: { consumed: number; budget: number }) {
  const pct = Math.min(100, Math.round((consumed / Math.max(1, budget)) * 100));
  const color = pct >= 80 ? "#dc2626" : pct >= 50 ? "#f59e0b" : "#16a34a";
  return (
    <div className="timer-wrap" data-testid="timer-bar">
      <div className="timer-track">
        <div
          className={`timer-fill${pct >= 80 ? " danger" : ""}`}
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className="timer-text">
        {consumed} / {budget} 字（{pct}%）
      </span>
    </div>
  );
}
