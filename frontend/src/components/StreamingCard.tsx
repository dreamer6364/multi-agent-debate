import { CAMP_COLOR } from "./SpeechCard";
import { roleDisplay } from "./SpeechCard";

/** 打字中卡片（v2.1 speech_delta）：逐字累积 + 闪烁光标；定稿事件到达后被 SpeechCard 替换。 */
export function StreamingCard({
  speaker,
  camp,
  text,
}: {
  speaker: string;
  camp: "pro" | "con" | "neutral";
  text: string;
}) {
  const color = CAMP_COLOR[camp] ?? CAMP_COLOR.neutral;
  return (
    <div
      data-testid="streaming-card"
      className="speech-card-live"
      style={{
        borderLeft: `4px solid ${color}`,
        background: "#fff",
        borderRadius: 8,
        padding: "10px 14px",
        marginBottom: 10,
        boxShadow: "0 1px 3px rgba(16,24,40,.08)",
      }}
    >
      <strong style={{ color }}>{roleDisplay(speaker)}</strong>
      <span style={{ color: "#98a2b3", fontSize: 12, marginLeft: 8 }}>正在发言…</span>
      <div style={{ whiteSpace: "pre-wrap", marginTop: 4 }}>
        {text}
        <span className="type-caret" />
      </div>
    </div>
  );
}
