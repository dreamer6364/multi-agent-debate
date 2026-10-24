import { Tag } from "antd";
import type { Camp, LiveSpeech } from "../api/types";

/** 阵营色彩（Neumorphism 柔和口径）：深色变体用于文字（对比度达标），原色仅作色点。 */
export const CAMP_COLOR: Record<Camp, string> = {
  pro: "#6d5dfc",
  con: "#ff6b6b",
  neutral: "#9aa2b1",
};
const CAMP_TEXT: Record<Camp, string> = {
  pro: "#4a3db8",
  con: "#c23a3a",
  neutral: "#566070",
};

const CN_NUM: Record<string, string> = { "1": "一", "2": "二", "3": "三", "4": "四" };
const JUDGE_CN: Record<string, string> = {
  "judge-logic": "逻辑评委",
  "judge-content": "内容评委",
  "judge-expression": "表达评委",
};

export function roleDisplay(role: string): string {
  if (role.startsWith("pro-")) return `正方·${CN_NUM[role.split("-")[1]]}辩`;
  if (role.startsWith("con-")) return `反方·${CN_NUM[role.split("-")[1]]}辩`;
  return JUDGE_CN[role] ?? { chair: "主席", timekeeper: "计时员" }[role] ?? role;
}

function speakerColor(role: string, camp: Camp): string {
  if (role.startsWith("judge-")) return "var(--judge-deep)";
  if (role === "timekeeper") return "var(--tk-deep)";
  if (role === "chair") return "var(--ink-2)";
  return CAMP_TEXT[camp];
}

/** 发言卡片（Neumorphism 凸起）：正文纯文本渲染（CONSTRAINTS F4：禁用 dangerouslySetInnerHTML，防 XSS）。 */
export function SpeechCard({ speech }: { speech: LiveSpeech }) {
  const camp = speech.camp;
  const dot = CAMP_COLOR[camp] ?? CAMP_COLOR.neutral;
  return (
    <div
      data-testid="speech-card"
      className="speech-card-anim"
      style={{
        background: "var(--neu-bg)",
        borderRadius: 16,
        padding: "12px 16px",
        marginBottom: 12,
        boxShadow: "4px 4px 8px var(--neu-dark), -4px -4px 8px var(--neu-light)",
      }}
    >
      <strong style={{ color: speakerColor(speech.speaker, camp) }}>
        <span
          style={{
            display: "inline-block", width: 8, height: 8, borderRadius: "50%",
            background: dot, marginRight: 8, verticalAlign: "middle",
          }}
        />
        {roleDisplay(speech.speaker)}
      </strong>
      <div style={{ whiteSpace: "pre-wrap", marginTop: 4, color: "var(--ink)" }}>{speech.text}</div>
      {speech.interrupted && <Tag color="volcano">已被打断</Tag>}
      {speech.side_notes.map((note) => (
        <Tag key={note} color={note.startsWith("违规") ? "volcano" : "default"}>
          {note}
        </Tag>
      ))}
    </div>
  );
}
