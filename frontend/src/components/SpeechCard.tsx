import { Tag } from "antd";
import type { Camp, LiveSpeech } from "../api/types";

// 角色配色 token（SPEC §8 / CONSTRAINTS F6 同源）：正方蓝、反方红、主席灰、计时黄、评委金
export const CAMP_COLOR: Record<Camp, string> = {
  pro: "#2563eb",
  con: "#dc2626",
  neutral: "#6b7280",
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

/** 发言卡片：正文纯文本渲染（CONSTRAINTS F4：禁用 dangerouslySetInnerHTML，防 XSS）。 */
export function SpeechCard({ speech, live = false }: { speech: LiveSpeech; live?: boolean }) {
  const color = CAMP_COLOR[speech.camp] ?? CAMP_COLOR.neutral;
  return (
    <div
      data-testid="speech-card"
      className={`speech-card-anim${live ? " speech-card-live" : ""}`}
      style={{
        borderLeft: `4px solid ${color}`,
        background: "#fff",
        borderRadius: 8,
        padding: "10px 14px",
        marginBottom: 10,
        boxShadow: "0 1px 3px rgba(16,24,40,.08)",
      }}
    >
      <strong style={{ color }}>{roleDisplay(speech.speaker)}</strong>
      <div style={{ whiteSpace: "pre-wrap", marginTop: 4 }}>{speech.text}</div>
      {speech.interrupted && <Tag color="red">已被打断</Tag>}
      {speech.side_notes.map((note) => (
        <Tag key={note} color={note.startsWith("违规") ? "volcano" : "default"}>
          {note}
        </Tag>
      ))}
    </div>
  );
}
