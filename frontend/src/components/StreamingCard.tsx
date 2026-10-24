import { CAMP_COLOR } from "./SpeechCard";
import { roleDisplay } from "./SpeechCard";

/** 打字中卡片（v2.1 speech_delta）：逐字累积 + 强调色光标；定稿事件到达后被 SpeechCard 替换。 */
export function StreamingCard({
  speaker,
  camp,
  text,
}: {
  speaker: string;
  camp: "pro" | "con" | "neutral";
  text: string;
}) {
  const dot = CAMP_COLOR[camp] ?? CAMP_COLOR.neutral;
  const nameColor = camp === "pro" ? "var(--accent-deep)" : camp === "con" ? "var(--con-deep)" : "var(--ink-2)";
  return (
    <div
      data-testid="streaming-card"
      className="speech-card-anim"
      style={{
        background: "var(--neu-bg)",
        borderRadius: 16,
        padding: "12px 16px",
        marginBottom: 12,
        boxShadow: "4px 4px 8px var(--neu-dark), -4px -4px 8px var(--neu-light)",
      }}
    >
      <strong style={{ color: nameColor }}>
        <span
          style={{
            display: "inline-block", width: 8, height: 8, borderRadius: "50%",
            background: dot, marginRight: 8, verticalAlign: "middle",
          }}
        />
        {roleDisplay(speaker)}
      </strong>
      <span className="speech-meta">正在发言…</span>
      <div style={{ whiteSpace: "pre-wrap", marginTop: 4, color: "var(--ink)" }}>
        {text}
        <span className="type-caret" />
      </div>
    </div>
  );
}
