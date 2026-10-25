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
  pro_total: number;
  con_total: number;
}

export interface Verdict {
  votes: { pro: number; con: number };
  team_totals: { pro: number | string; con: number | string };
  winner: "pro" | "con" | null;
  best_speaker: string | null;
  decisive_rule: string;
  scorecards?: ScorecardSummary[];
}

export interface DebateDetail {
  id: number;
  topic: { id: number; title: string };
  format: DebateFormat;
  status: DebateStatus;
  cfg: Record<string, unknown>;
  winner: "pro" | "con" | null;
  best_speaker: string | null;
  team_totals: { pro: string | null; con: string | null };
  votes: { pro?: number; con?: number } | null;
  usage: { calls?: number; prompt_tokens?: number; completion_tokens?: number } | null;
  error: string | null;
  scorecards?: ScorecardSummary[];
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
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
