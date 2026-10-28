import { useMemo } from "react";
import type { Camp, DebateFormat, LiveSpeech } from "../api/types";
import { FULL_PHASES, phasesOf } from "./phases";

/**
 * 辩论流程同步图（WorkflowGraph）
 *
 * 与直播流实时联动：每个环节一行，正方在左、反方在右、主席/评委居中；
 * 当前环节高亮脉冲，该环节已产生的发言条数与"谁刚说完话"实时更新，
 * 质询/攻辩行额外画出「谁问谁」的双向箭头。点击任一环节可跳回该环节的发言记录。
 *
 * 版式刻意不依赖图表库：纯 SVG + CSS，零新增依赖，且尺寸随宽度自适应。
 */

/* ---------- 画布常量 ---------- */
const VW = 960;
const PAD_X = 20;
const CARD_W = 396;
const PRO_X = PAD_X;
const CON_X = VW - PAD_X - CARD_W;
const MID_X = VW / 2;
const CARD_H = 62;
const SOLO_H = 58;
const HEAD_H = 46;
const ROW_GAP = 14;
const SOLO_INSET = 150;

/* ---------- 主题色（与 index.css 的 neumorphism 变量一致） ---------- */
const INK = "#333333";
const INK2 = "#5f697a";
const INK3 = "#9aa2b1";
const PRO = "#6d5dfc";
const PRO_DEEP = "#4a3db8";
const CON = "#ff6b6b";
const CON_DEEP = "#c23a3a";
const JUDGE = "#8a6a1c";
const CHAIR = "#6b7688";
const CARD_BG = "#e7ebf2";
const CARD_BG_ACTIVE = "#eef0fa";
const BORDER = "#cbd2de";
const PAGE_BG = "#e0e5ec";

const CIRCLED = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩", "⑪"];

/** 单侧席位：标题 + 说明 + 先后手（与引擎 MONOLOGUE_SPEAKERS 的真实顺序一致） */
interface Slot {
  title: string;
  note: string;
}

interface PhasePlan {
  phase: number;
  name: string;
  /** duplex = 正反双方对置；solo = 主席/评委占整行 */
  layout: "duplex" | "solo";
  pro?: Slot;
  con?: Slot;
  center?: Slot;
  /** 交叉环节：中间画「谁向谁」的双向箭头 */
  cross?: string;
}

const PLAN: Record<number, PhasePlan> = {
  1: {
    phase: 1, name: "主席开场", layout: "solo",
    center: { title: "中立主席", note: "引题演讲（赛前导览）→ 报幕宣布比赛开始" },
  },
  2: {
    phase: 2, name: "开篇立论", layout: "duplex",
    pro: { title: "正方一辩", note: "先手 · 定义权 / 判准 / 2~3 个论点" },
    con: { title: "反方一辩", note: "后手 · 定义权 / 判准 / 2~3 个论点" },
  },
  3: {
    phase: 3, name: "质询一辩", layout: "duplex", cross: "质询",
    pro: { title: "正方二辩 提问", note: "→ 反方一辩作答（只答不问）" },
    con: { title: "反方二辩 提问", note: "→ 正方一辩作答（只答不问）" },
  },
  4: {
    phase: 4, name: "驳论", layout: "duplex",
    pro: { title: "正方二辩", note: "后手 · 拆反方立论 / 补强己方" },
    con: { title: "反方二辩", note: "先手 · 拆正方立论 / 补强己方" },
  },
  5: {
    phase: 5, name: "攻辩盘问", layout: "duplex", cross: "盘问",
    pro: { title: "正方三辩 盘问", note: "连问反方一 / 二 / 四辩" },
    con: { title: "反方三辩 盘问", note: "连问正方一 / 二 / 四辩" },
  },
  6: {
    phase: 6, name: "攻辩小结", layout: "duplex",
    pro: { title: "正方三辩", note: "先手 · 收束攻防 / 固化得分点" },
    con: { title: "反方三辩", note: "后手 · 收束攻防 / 反打漏洞" },
  },
  7: {
    phase: 7, name: "自由辩论", layout: "duplex",
    pro: { title: "正方（先手）", note: "轮转 二→三→一→四辩 · 双池计时" },
    con: { title: "反方（后手）", note: "轮转 二→三→一→四辩 · 双池计时" },
  },
  8: {
    phase: 8, name: "总结陈词", layout: "duplex",
    pro: { title: "正方四辩", note: "后手 · 回应全场 / 收束判准与价值" },
    con: { title: "反方四辩", note: "先手 · 回应全场 / 收束判准与价值" },
  },
  9: {
    phase: 9, name: "评委点评", layout: "solo",
    center: { title: "评委团 ×3 并行", note: "机器质检 ∥ 评审 → 评分卡与理由" },
  },
  10: {
    phase: 10, name: "宣判闭幕", layout: "solo",
    center: { title: "主席宣判", note: "投票 → 团体总分 → 决胜（绝不平局）" },
  },
  11: {
    phase: 11, name: "主席致辞", layout: "solo",
    center: { title: "主席致辞", note: "致谢双方与评委 · 宣布结束（不得改判）" },
  },
};

/** 发言所属环节：优先 phase 字段，回落到 phase_name（旧数据/漏事件的兜底） */
function phaseOf(s: LiveSpeech): number | null {
  if (typeof s.phase === "number") return s.phase;
  const hit = FULL_PHASES.find((p) => p.name === s.phase_name);
  return hit ? hit.phase : null;
}

/** 中文按字宽 1 估、ASCII 按 0.55 估（SVG 无自动换行，标签底衬需要宽度） */
function estWidth(text: string, fontSize: number): number {
  let w = 0;
  for (const ch of text) w += /[\u2460-\u24ff\u3000-\u9fff\uff00-\uffef]/.test(ch) ? fontSize : fontSize * 0.55;
  return w;
}

type RowState = "done" | "active" | "pending";

export function WorkflowGraph({
  format,
  currentPhase,
  speeches,
  status,
  streamingCamp,
  streamingSpeaker,
  judgingNote,
  winner,
  onSelectPhase,
  onSelectVerdict,
}: {
  format: DebateFormat;
  currentPhase: number | null;
  speeches: LiveSpeech[];
  status: string | null;
  streamingCamp?: Camp | null;
  streamingSpeaker?: string | null;
  /** 评审环节的实时进度文案（来自 judging_progress 心跳），其它环节传 null */
  judgingNote?: string | null;
  /** 终态胜方：undefined = 尚无判决；null = 平局 */
  winner?: "pro" | "con" | null;
  onSelectPhase?: (phase: number) => void;
  /** 终态点击胜方徽标 → 跳「评委打分」看判决依据 */
  onSelectVerdict?: () => void;
}) {
  const terminal = status !== null && ["finished", "failed", "stopped"].includes(status);

  // 按环节聚合：条数 / 最近发言方（用于「谁刚说完」的活体指示）
  const stats = useMemo(() => {
    const map = new Map<number, { pro: number; con: number; center: number; last: LiveSpeech | null }>();
    for (const s of speeches) {
      const p = phaseOf(s);
      if (p === null) continue;
      const row = map.get(p) ?? { pro: 0, con: 0, center: 0, last: null };
      if (s.camp === "pro") row.pro += 1;
      else if (s.camp === "con") row.con += 1;
      else row.center += 1;
      row.last = s;
      map.set(p, row);
    }
    return map;
  }, [speeches]);

  const rows = useMemo(() => {
    const items: Array<{ plan: PhasePlan; y: number; top: number }> = [];
    let cursor = 12;
    for (const p of phasesOf(format)) {
      const plan = PLAN[p.phase];
      if (!plan) continue;
      const h = plan.layout === "solo" ? SOLO_H : CARD_H;
      items.push({ plan, y: cursor, top: cursor + HEAD_H });
      cursor += HEAD_H + h + ROW_GAP;
    }
    return { items, height: cursor - ROW_GAP + 10 };
  }, [format]);

  // 有效环节：直播用 store 的 phase；归档（无事件流）退化为「最后一个有发言的环节」
  const livePhase = useMemo(() => {
    if (currentPhase !== null) return currentPhase;
    let max = -1;
    for (const [p, v] of stats) {
      if (v.pro + v.con + v.center > 0 && p > max) max = p;
    }
    return max === -1 ? null : max;
  }, [currentPhase, stats]);

  const stateOf = (plan: PhasePlan): RowState => {
    const v = stats.get(plan.phase);
    const touched = !!v && v.pro + v.con + v.center > 0;
    if (terminal) {
      // 正常完赛：赛程内全部环节都跑过；异常/中止：只把真正产出过发言的环节标为完成
      if (status === "finished") {
        return touched || (livePhase !== null && plan.phase <= livePhase) ? "done" : "pending";
      }
      return touched ? "done" : "pending";
    }
    if (livePhase === null) return touched ? "done" : "pending";
    if (plan.phase === livePhase) return "active";
    return plan.phase < livePhase ? "done" : "pending";
  };

  const activePlan = livePhase === null ? null : rows.items.find((r) => r.plan.phase === livePhase)?.plan ?? null;
  const doneCount = rows.items.filter((r) => stateOf(r.plan) === "done").length;
  const total = rows.items.length;
  const stepNo = activePlan ? rows.items.findIndex((r) => r.plan.phase === activePlan.phase) + 1 : doneCount;

  const sideMeta = (side: "pro" | "con", plan: PhasePlan, state: RowState) => {
    const v = stats.get(plan.phase);
    const count = side === "pro" ? v?.pro ?? 0 : v?.con ?? 0;
    const speaking = state === "active" && streamingCamp === side;
    const hint = speaking
      ? `${streamingSpeaker ?? "正在"} 发言中`
      : count > 0
        ? `已发言 ${count} 条`
        : state === "pending" ? "待发言" : "未发言";
    return { count, hint, speaking };
  };

  const renderSlot = (
    plan: PhasePlan, side: "pro" | "con", x: number, y: number, state: RowState,
  ) => {
    const slot = side === "pro" ? plan.pro : plan.con;
    if (!slot) return null;
    const meta = sideMeta(side, plan, state);
    const color = side === "pro" ? PRO : CON;
    const deep = side === "pro" ? PRO_DEEP : CON_DEEP;
    const pending = state === "pending";
    const active = state === "active";
    return (
      <g
        key={`${plan.phase}-${side}`}
        className="wf-card"
        data-testid={`wf-slot-${plan.phase}-${side}`}
        onClick={() => onSelectPhase?.(plan.phase)}
        style={{ cursor: onSelectPhase ? "pointer" : "default" }}
        role={onSelectPhase ? "button" : undefined}
      >
        <title>{`${plan.name} · ${slot.title}｜${slot.note}${meta.count ? `｜已发言 ${meta.count} 条` : ""}`}</title>
        <rect
          x={x} y={y} width={CARD_W} height={CARD_H} rx={14}
          fill={active ? CARD_BG_ACTIVE : CARD_BG}
          stroke={pending ? BORDER : active ? color : "transparent"}
          strokeWidth={active ? 2 : 1}
          strokeDasharray={pending ? "5 4" : undefined}
          opacity={pending ? 0.72 : 1}
        />
        {active && (
          <rect
            className="wf-pulse" x={x - 3} y={y - 3} width={CARD_W + 6} height={CARD_H + 6} rx={17}
            fill="none" stroke={color} strokeWidth={2}
          />
        )}
        <rect x={x + 12} y={y + 15} width={4} height={CARD_H - 30} rx={2} fill={color} opacity={pending ? 0.45 : 0.95} />
        <text x={x + 26} y={y + 27} fontSize={14.5} fontWeight={700} fill={pending ? INK3 : INK}>
          {slot.title}
        </text>
        <text x={x + 26} y={y + 47} fontSize={12} fill={pending ? INK3 : INK2}>
          {slot.note}
        </text>
        {meta.count > 0 && (
          <text x={x + CARD_W - 16} y={y + 27} fontSize={12.5} fontWeight={700} fill={deep} textAnchor="end">
            {meta.count} 条
          </text>
        )}
        <text
          x={x + CARD_W - 16} y={y + 47} fontSize={11.5} textAnchor="end"
          fill={meta.speaking ? deep : pending ? INK3 : INK2}
          fontWeight={meta.speaking ? 700 : 400}
        >
          {meta.speaking ? "● " : ""}{meta.hint}
        </text>
      </g>
    );
  };

  const renderSolo = (plan: PhasePlan, y: number, state: RowState) => {
    const slot = plan.center;
    if (!slot) return null;
    const v = stats.get(plan.phase);
    const count = (v?.pro ?? 0) + (v?.con ?? 0) + (v?.center ?? 0);
    const color = plan.phase === 9 ? JUDGE : CHAIR;
    const pending = state === "pending";
    const active = state === "active";
    const x = SOLO_INSET;
    const w = VW - SOLO_INSET * 2;
    return (
      <g
        key={`solo-${plan.phase}`}
        className="wf-card"
        data-testid={`wf-slot-${plan.phase}-center`}
        onClick={() => onSelectPhase?.(plan.phase)}
        style={{ cursor: onSelectPhase ? "pointer" : "default" }}
        role={onSelectPhase ? "button" : undefined}
      >
        <title>{`${plan.name} · ${slot.title}｜${slot.note}`}</title>
        <rect
          x={x} y={y} width={w} height={SOLO_H} rx={14}
          fill={active ? CARD_BG_ACTIVE : CARD_BG}
          stroke={pending ? BORDER : active ? color : "transparent"}
          strokeWidth={active ? 2 : 1}
          strokeDasharray={pending ? "5 4" : undefined}
          opacity={pending ? 0.72 : 1}
        />
        {active && (
          <rect
            className="wf-pulse" x={x - 3} y={y - 3} width={w + 6} height={SOLO_H + 6} rx={17}
            fill="none" stroke={color} strokeWidth={2}
          />
        )}
        <rect x={x + 12} y={y + 14} width={4} height={SOLO_H - 28} rx={2} fill={color} opacity={pending ? 0.45 : 0.95} />
        <text x={x + 26} y={y + 25} fontSize={14} fontWeight={700} fill={pending ? INK3 : INK}>
          {slot.title}
        </text>
        <text x={x + 26} y={y + 44} fontSize={12} fill={pending ? INK3 : INK2}>
          {slot.note}
        </text>
        <text
          x={x + w - 16} y={y + 25} fontSize={12.5} fontWeight={700} textAnchor="end"
          fill={pending ? INK3 : plan.phase === 9 ? JUDGE : CHAIR}
        >
          {count > 0 ? `${count} 条` : ""}
        </text>
        <text x={x + w - 16} y={y + 44} fontSize={11.5} textAnchor="end" fill={pending ? INK3 : INK2}>
          {active && plan.phase === 9 && judgingNote
            ? judgingNote
            : active ? "进行中" : state === "done" ? "已完成" : "未开始"}
        </text>
      </g>
    );
  };

  const currentLabel = terminal
    ? status === "finished" ? "已结束" : status === "stopped" ? "已终止" : "异常终止"
    : activePlan ? activePlan.name : "待开始";

  return (
    <div className="wf-wrap" data-testid="workflow-graph">
      <div className="wf-head">
        <div className="wf-title">
          🗺 流程同步图
          <span className="wf-sub">与直播实时同步 · 点击任一环节跳转发言记录</span>
        </div>
        <div className="wf-legend">
          <span className="wf-key"><i style={{ background: PRO }} />正方</span>
          <span className="wf-key"><i style={{ background: CON }} />反方</span>
          <span className="wf-key"><i style={{ background: CHAIR }} />主席</span>
          <span className="wf-key"><i style={{ background: JUDGE }} />评委</span>
        </div>
      </div>

      <div className="wf-status" data-testid="wf-status">
        <span className="wf-step">
          {terminal
            ? `全部环节 ${doneCount}/${total} · ${currentLabel}`
            : activePlan
              ? `第 ${stepNo}/${total} 步 · ${currentLabel}`
              : `尚未开始 · 共 ${total} 个环节`}
        </span>
        {!terminal && activePlan && (
          <span className="wf-live">
            <span className="wf-dot" />
            {streamingSpeaker ? `${streamingSpeaker} 正在发言` : "该环节进行中"}
          </span>
        )}
        <span className="wf-bar" aria-hidden>
          {rows.items.map((r) => {
            const st = stateOf(r.plan);
            return (
              <i
                key={r.plan.phase}
                className={`wf-seg ${st}`}
                style={st === "active" ? { background: PRO } : undefined}
              />
            );
          })}
        </span>
        {terminal && winner !== undefined && (
          <button
            type="button"
            className={`wf-winner ${winner ?? "draw"}`}
            data-testid="wf-winner"
            onClick={onSelectVerdict}
            title={winner ? "查看评委打分与判决依据" : "本场平局"}
          >
            {winner ? `🏆 ${winner === "pro" ? "正方" : "反方"}胜` : "⚖️ 平局"}
          </button>
        )}
      </div>

      <svg
        viewBox={`0 0 ${VW} ${rows.height}`}
        width="100%"
        role="group"
        aria-label="辩论流程同步图"
        style={{ display: "block", height: "auto" }}
      >
        <defs>
          <marker
            id="wf-arrow" viewBox="0 0 10 10" refX="8" refY="5"
            markerWidth="6" markerHeight="6" orient="auto-start-reverse"
          >
            <path d="M0,0 L10,5 L0,10 z" fill={INK3} />
          </marker>
        </defs>

        {rows.items.map(({ plan, y, top }) => {
          const state = stateOf(plan);
          const base = `${CIRCLED[plan.phase - 1] ?? plan.phase} ${plan.name}`;
          const label = state === "active" ? `${base} · 进行中` : base;
          const pw = estWidth(label, 12.5) + 26;
          return (
            <g key={`row-${plan.phase}`} data-testid={`wf-phase-${plan.phase}`} data-state={state}>
              <rect
                x={MID_X - pw / 2} y={y + 2} width={pw} height={21} rx={10.5}
                fill={PAGE_BG}
              />
              <text
                x={MID_X - (state === "active" ? 6 : 0)} y={y + 17}
                fontSize={12.5} textAnchor="middle"
                fontWeight={state === "active" ? 700 : 500}
                fill={state === "active" ? PRO_DEEP : state === "done" ? INK2 : INK3}
              >
                {label}
              </text>
              {state === "active" && (
                <circle className="wf-dot-svg" cx={MID_X + pw / 2 - 13} cy={y + 12.5} r={3.6} fill={PRO} />
              )}
              {/* 段头箭头：从标签指向本行卡片，替代原先穿过标签的主干连线 */}
              <line
                x1={MID_X} y1={y + 27} x2={MID_X} y2={y + HEAD_H - 5}
                stroke={state === "pending" ? BORDER : state === "active" ? PRO : "#b9c1cf"}
                strokeWidth={state === "active" ? 1.8 : 1.4}
                markerEnd="url(#wf-arrow)"
              />
              {plan.layout === "solo"
                ? renderSolo(plan, top, state)
                : (
                  <>
                    {plan.cross && (
                      <g>
                        <line
                          x1={PRO_X + CARD_W + 10} y1={top + CARD_H / 2}
                          x2={CON_X - 10} y2={top + CARD_H / 2}
                          stroke={state === "active" ? INK2 : INK3}
                          strokeWidth={state === "active" ? 1.9 : 1.5}
                          markerStart="url(#wf-arrow)"
                          markerEnd="url(#wf-arrow)"
                        />
                        <rect
                          x={MID_X - 26} y={top + CARD_H / 2 - 10} width={52} height={19} rx={9.5}
                          fill={PAGE_BG}
                        />
                        <text
                          x={MID_X} y={top + CARD_H / 2 + 4} fontSize={11} textAnchor="middle"
                          fill={state === "active" ? INK : INK2} fontWeight={600}
                        >
                          {plan.cross}
                        </text>
                      </g>
                    )}
                    {renderSlot(plan, "pro", PRO_X, top, state)}
                    {renderSlot(plan, "con", CON_X, top, state)}
                  </>
                )}
            </g>
          );
        })}
      </svg>

      <p className="wf-note">
        左右两栏为同一环节的正反双方席位，箭头标出「谁向谁提问」；虚线框表示尚未开始。
        {terminal && " 本场已结束，流程为最终归档状态。"}
      </p>
    </div>
  );
}
