import { Tag, Tooltip } from "antd";
import type { Camp, FallacyMark, LiveSpeech } from "../api/types";
import { useDebateStore } from "../stores/debateStore";
import { PHASE_TAG, fmtDuration, phaseKind, WORDS_PER_MINUTE } from "./phases";
import { RichText } from "./RichText";

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
const CAMP_NAME: Record<Camp, string> = { pro: "正方", con: "反方", neutral: "中立方" };

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

/** 单条谬误的 tooltip 内容：类型 + 原文片段 + 解释 + 置信度 */
function FallacyTip({ mark }: { mark: FallacyMark }) {
  return (
    <div style={{ maxWidth: 320, textAlign: "left" }}>
      <div style={{ fontWeight: 700, marginBottom: 4 }}>⚠️ {mark.name}</div>
      {mark.quote && (
        <div style={{ color: "#f0d090", fontSize: 12, marginBottom: 4 }}>原文：“{mark.quote}”</div>
      )}
      <div style={{ fontSize: 12, lineHeight: 1.6 }}>{mark.explanation}</div>
      {typeof mark.confidence === "number" && (
        <div style={{ fontSize: 11, opacity: 0.75, marginTop: 4 }}>置信度 {Math.round(mark.confidence * 100)}%</div>
      )}
    </div>
  );
}

/**
 * 发言卡片（辩论专用版）：
 * - 元信息：角色标签 + 环节标签【立论/质询/驳论/自由辩/结辩】+ 预计时长
 * - 样式分层：立论/结辩厚重、质询/驳论中等、自由辩轻量、评委灰绿独立样式
 * - 谬误标注：右下角 ⚠️，hover 展示类型/原文/解释（Elenchus 结果按 seq 挂载）
 * 正文纯文本渲染（CONSTRAINTS F4：禁用 dangerouslySetInnerHTML，防 XSS）。
 */
export function SpeechCard({ speech, active = false }: { speech: LiveSpeech; active?: boolean }) {
  const marks = useDebateStore((s) => s.fallaciesBySeq[speech.seq]);
  const camp = speech.camp;
  const dot = CAMP_COLOR[camp] ?? CAMP_COLOR.neutral;
  const kind = phaseKind(speech.phase, camp);
  const phaseTag = speech.phase ? PHASE_TAG[speech.phase] : undefined;
  const estSeconds = Math.round(((speech.word_count ?? 0) / WORDS_PER_MINUTE) * 60);
  return (
    <div
      data-testid="speech-card"
      className={[
        "speech-card-anim",
        "sp-card",
        `sp-${kind}`,
        camp === "pro" ? "sp-pro" : camp === "con" ? "sp-con" : "sp-neutral",
        active ? "sp-active" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      style={{ ["--camp" as string]: dot, ["--camp-text" as string]: CAMP_TEXT[camp] }}
    >
      <div className="sp-meta-row">
        <strong className="sp-speaker" style={{ color: speakerColor(speech.speaker, camp) }}>
          <span className="sp-dot" style={{ background: dot }} />
          {roleDisplay(speech.speaker)}
        </strong>
        {camp !== "neutral" && <Tag className="sp-role-tag">{CAMP_NAME[camp]}</Tag>}
        {phaseTag && <Tag className={`sp-phase-tag tag-${kind}`}>【{phaseTag}】</Tag>}
        {(speech.word_count ?? 0) > 0 && <span className="sp-meta">≈ {fmtDuration(estSeconds)}</span>}
        <span className="sp-meta">#{speech.seq}</span>
      </div>
      <div className="sp-body" data-testid="speech-body">
        <RichText text={speech.text} />
      </div>
      <div className="sp-flags">
        {speech.interrupted && <Tag color="volcano">已被打断</Tag>}
        {speech.side_notes.map((note, i) => (
          // v2.9.4：key 用序号——side_notes 可能含重复文本（如两次计时警告），key={note} 会撞 key
          <Tag key={`${note}-${i}`} color={note.startsWith("违规") ? "volcano" : "default"}>
            {note}
          </Tag>
        ))}
        {marks && marks.length > 0 && (
          <span className="sp-fallacy-wrap">
            {marks.map((m, i) => (
              <Tooltip key={`${m.name}-${i}`} title={<FallacyTip mark={m} />}>
                <span className="sp-fallacy-badge" data-testid="fallacy-badge">
                  ⚠️ {m.name}
                </span>
              </Tooltip>
            ))}
          </span>
        )}
      </div>
    </div>
  );
}
