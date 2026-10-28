import { useEffect, useMemo, useState } from "react";
import { Tag } from "antd";
import type { GraphSnapshot } from "../api/types";
import { MermaidCanvas } from "./MermaidCanvas";

/**
 * 辩论动态图面板（v2.5）
 *
 * 两套视图各回答一个问题：
 *   工作流控制图 —— Agent 被怎么调度（发言 → 裁判判断 → 继续/追加质询/提前结束）
 *   论证关系图   —— 论点怎么长出来（新主张成节点，反驳/质询成虚线，支持成实线）
 *
 * 图本身**不在这里拼**：源码由后端按辩论状态现场生成，本组件只负责选版本、渲染与标注变更。
 */

type ViewKey = "control" | "argument";

const BRANCH_CN: Record<string, string> = {
  next_round: "继续下一轮",
  extra_query: "追加质询",
  early_finish: "提前结束",
  terminate: "终止",
};

const ROLE_CN: Record<string, string> = {
  "pro-1": "正方一辩", "pro-2": "正方二辩", "pro-3": "正方三辩", "pro-4": "正方四辩",
  "con-1": "反方一辩", "con-2": "反方二辩", "con-3": "反方三辩", "con-4": "反方四辩",
  chair: "主席", "judge-logic": "逻辑评委", "judge-content": "内容评委", "judge-expression": "表达评委",
};

export function speakerLabel(role: string): string {
  return ROLE_CN[role] ?? role;
}

export interface DebateGraphPanelProps {
  graph: GraphSnapshot | null;
  history: GraphSnapshot[];
  /** 只用于空态文案（pending 与已开赛的提示不同），接受 store 的 null 形态 */
  status: string | null;
}

export function DebateGraphPanel({ graph, history, status }: DebateGraphPanelProps) {
  const [view, setView] = useState<ViewKey>("control");
  // null = 跟随最新一版；数字 = 正在回看的历史版本下标
  const [cursor, setCursor] = useState<number | null>(null);

  // 有新版本进来时若正在回看旧版，保持原位（不打断阅读）；用户点"回到最新"才跳回
  useEffect(() => {
    if (cursor !== null && cursor >= history.length) setCursor(null);
  }, [history.length, cursor]);

  const current: GraphSnapshot | null = useMemo(() => {
    if (!history.length) return graph;
    if (cursor === null) return history[history.length - 1];
    return history[Math.min(cursor, history.length - 1)];
  }, [graph, history, cursor]);

  const index = current && history.length ? history.findIndex((s) => s.version === current.version) : -1;
  const atLatest = cursor === null || index === history.length - 1;

  const changes = useMemo(() => {
    if (!current) return { nodes: 0, attacks: 0, supports: 0, queries: 0 };
    const kinds = current.added_edge_kinds ?? [];
    return {
      nodes: current.added_nodes?.length ?? 0,
      attacks: kinds.filter((k) => k === "attack").length,
      supports: kinds.filter((k) => k === "support").length,
      queries: kinds.filter((k) => k === "query").length,
    };
  }, [current]);

  if (!current) {
    return (
      <div className="dg-empty" data-testid="debate-graph-empty">
        <p className="dg-empty-title">🗺 辩论图随辩论自动生长</p>
        <p className="dg-empty-sub">
          {status === "pending"
            ? "比赛尚未开始。开赛后，每条发言都会为论证图添一个节点、为控制图添一步调度。"
            : "正在等待第一条发言……图由后端按当前辩论状态现场生成，不是预置的固定流程图。"}
        </p>
      </div>
    );
  }

  const state = current.state;

  return (
    <div className="dg" data-testid="debate-graph-panel">
      <div className="dg-status" data-testid="dg-state">
        <span className="dg-round">
          第 <b>{state.round}</b> 轮
        </span>
        <span className="dg-chip pro">正方 {speakerLabel(state.pro_agent) || "待发言"}</span>
        <span className="dg-chip con">反方 {speakerLabel(state.con_agent) || "待发言"}</span>
        <span className="dg-chip neutral">裁判 {speakerLabel(state.moderator) || "主席"}</span>
        {state.current_claim && (
          <span className="dg-claim" title={state.current_claim}>
            当前论点：{state.current_claim}
          </span>
        )}
        <span className={`dg-branch ${state.branch}`}>{BRANCH_CN[state.branch] ?? state.branch}</span>
        {state.converged && <Tag color="blue">已收敛</Tag>}
        {state.terminated && <Tag color="red">已终止</Tag>}
      </div>

      <div className="dg-bar">
        <div className="dg-views" role="tablist">
          <button
            type="button" role="tab" aria-selected={view === "control"}
            className={`dg-view ${view === "control" ? "on" : ""}`}
            onClick={() => setView("control")}
          >
            工作流控制图
          </button>
          <button
            type="button" role="tab" aria-selected={view === "argument"}
            className={`dg-view ${view === "argument" ? "on" : ""}`}
            onClick={() => setView("argument")}
          >
            论证关系图
          </button>
        </div>

        <div className="dg-delta" data-testid="dg-delta">
          {changes.nodes > 0 && <span className="dg-new">+{changes.nodes} 节点</span>}
          {changes.attacks > 0 && <span className="dg-atk">+{changes.attacks} 攻击边</span>}
          {changes.supports > 0 && <span className="dg-sup">+{changes.supports} 支持边</span>}
          {changes.queries > 0 && <span className="dg-qry">+{changes.queries} 质询边</span>}
          {changes.nodes === 0 && changes.attacks === 0 && changes.supports === 0 && changes.queries === 0 && (
            <span className="dg-nochange">本版无新增论点</span>
          )}
        </div>
      </div>

      <MermaidCanvas
        code={current.views[view]}
        version={`${current.version}-${view}`}
        height={view === "control" ? 540 : 480}
        testId="dg-canvas"
        emptyHint="该版本没有图内容"
      />

      <div className="dg-history" data-testid="dg-history">
        <button
          type="button" className="dg-nav" disabled={index <= 0}
          onClick={() => setCursor(Math.max(0, index - 1))}
        >
          ◀ 上一版
        </button>
        <input
          type="range" min={0} max={Math.max(0, history.length - 1)} value={Math.max(0, index)}
          onChange={(e) => setCursor(Number(e.target.value))}
          aria-label="历史图版本"
        />
        <button
          type="button" className="dg-nav" disabled={index >= history.length - 1}
          onClick={() => setCursor(Math.min(history.length - 1, index + 1))}
        >
          下一版 ▶
        </button>
        <span className="dg-ver">
          第 {index + 1}/{history.length} 版 · 第 {current.round} 轮
        </span>
        {!atLatest && (
          <button type="button" className="dg-live" onClick={() => setCursor(null)} data-testid="dg-back-live">
            回到最新
          </button>
        )}
      </div>

      <p className="dg-note">
        节点、连线、高亮全部由后端依据当前辩论状态实时生成：实线＝支持，虚线＝反驳/质询；
        当前活跃节点描边加粗，走完的节点置灰，本轮新增节点描金边。
        图比画布宽时可拖动画布平移，Ctrl／⌘ + 滚轮缩放，右侧「适应」回到按宽度铺满。
      </p>
    </div>
  );
}
