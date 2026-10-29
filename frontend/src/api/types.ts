// 与后端 JSON 字段对齐：snake_case（CONSTRAINTS §3.1，TypeScript 接口字段跟随后端）

export type Camp = "pro" | "con" | "neutral";
export type DebateStatus = "pending" | "running" | "finished" | "failed" | "stopped";
export type DebateFormat = "full" | "lite";

export interface Speech {
  seq: number;
  phase: number;
  phase_name: string;
  speaker: string;
  camp: Camp;
  text: string;
  side_notes: string[];
  interrupted: boolean;
  word_count: number;
  tokens: number;
  fallacies?: FallacyMark[];
}

/** v1.7 Elenchus 谬误标注（挂载到发言 seq；SSE fallacy 事件与 REST speeches 同构） */
export interface FallacyMark {
  seq: number;
  camp: Camp;
  speaker: string;
  name: string;
  quote: string;
  explanation: string;
  confidence?: number;
}

/** SSE speech 事件载荷（SPEC §6.2，字段封闭；word_count 为 v2.1 增补） */
export interface LiveSpeech {
  seq: number;
  speaker: string;
  camp: Camp;
  text: string;
  interrupted: boolean;
  side_notes: string[];
  word_count?: number;
  phase?: number;
  phase_name?: string;
  fallacies?: FallacyMark[];
}

/** SSE speech_delta 事件载荷（v2.1 流式直播；不落库不重放） */
export interface SpeechDeltaEvent {
  seq: number;
  delta: string; // "" 表示 attempt 重置信号（清空该 seq 累积文本）
  attempt: number;
  speaker: string;
  camp: Camp;
}

export interface PhaseStartEvent {
  phase: number;
  name: string;
  actors: string[];
  budget_words: number;
}

export interface TimerEvent {
  type: "warning" | "time_up";
  speaker: string | null;
}

export interface ScorecardSummary {
  judge: string;
  focus: string;
  vote: "pro" | "con" | null;
  best_speaker: string | null;
  abstained: boolean;
  /** v2.6：弃权但带点评 = 系统按实录+质检标注兜底生成（引擎 card.rescued 透传） */
  rescued?: boolean;
  pro_total: number;
  con_total: number;
  /** 分维明细（statement/rebuttal_cross/attack/free/closing/conduct）——评委打分的具体依据 */
  pro_scores?: Record<string, number>;
  con_scores?: Record<string, number>;
  /** 评委理由：亮点/不足（须引原话），键为 "pro" / "con" */
  highlights?: { pro?: string[]; con?: string[] };
  weaknesses?: { pro?: string[]; con?: string[] };
}

export interface Verdict {
  votes: { pro: number; con: number };
  team_totals: { pro: number | string; con: number | string };
  winner: "pro" | "con" | null;
  best_speaker: string | null;
  /** majority | team_total | best_speaker | final_judge | unresolved */
  decisive_rule: string;
  /** 智能终裁说明（决胜依据为 final_judge 时存在） */
  final_reason?: string | null;
  scorecards?: ScorecardSummary[];
}

export interface DebateDetail {
  id: number;
  topic: {
    id: number;
    title: string;
    pro_stance?: string;
    con_stance?: string;
    pro_points?: string[];
    con_points?: string[];
  };
  format: DebateFormat;
  status: DebateStatus;
  cfg: Record<string, unknown>;
  winner: "pro" | "con" | null;
  best_speaker: string | null;
  team_totals: { pro: string | null; con: string | null };
  votes: { pro?: number; con?: number } | null;
  decisive_rule?: string | null;
  final_reason?: string | null;
  usage: { calls?: number; prompt_tokens?: number; completion_tokens?: number } | null;
  error: string | null;
  scorecards?: ScorecardSummary[];
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  /** v2.7 复盘泳道总结（详情接口按落库实录现算；直播中由 SSE swimlane 事件更新） */
  swimlane?: SwimlanePayload | null;
}

export interface TopicItem {
  id: number;
  title: string;
  pro_stance: string;
  con_stance: string;
  pro_points: string;
  con_points: string;
  is_builtin: boolean;
  owner_id: number | null;
  created_at: string;
}

export interface SpeechPage {
  items: Speech[];
  has_more: boolean;
}

export interface MeInfo {
  id: number;
  username: string;
  role: string;
  quota: { concurrent_limit: number; daily_limit: null };
}

/** v2.7 复盘泳道总结：阶段 × 阵营泳道（正方/反方/公共场域） */
export interface SwimlaneEntry {
  speaker: string;
  action: string;
  content: string;
  /** 指向对象（如「反方一辩（反驳）」） */
  points_to: string[];
  /** 被谁指向 */
  pointed_by: string[];
  seq: number | null;
}

export interface SwimlanePublic {
  receivers: string;
  timer: string;
  notes: string[];
}

export interface SwimlaneRow {
  phase: number;
  name: string;
  pro: SwimlaneEntry[];
  con: SwimlaneEntry[];
  public: SwimlanePublic;
}

export interface SwimlanePayload {
  topic: string;
  rows: SwimlaneRow[];
}

