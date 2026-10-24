import { useQuery } from "@tanstack/react-query";
import { Button, Space, Tag } from "antd";
import { useEffect, useMemo, useRef } from "react";
import { useParams } from "react-router-dom";
import { getDebate, stopDebate } from "../api/client";
import type { Verdict } from "../api/types";
import { PhaseTimeline } from "../components/PhaseTimeline";
import { ScoreBoard } from "../components/ScoreBoard";
import { SpeechCard } from "../components/SpeechCard";
import { StreamingCard } from "../components/StreamingCard";
import { TimerBar } from "../components/TimerBar";
import { VerdictBanner } from "../components/VerdictBanner";
import { useDebateStream } from "../hooks/useDebateStream";
import { useDebateStore } from "../stores/debateStore";

const STATUS_CN: Record<string, string> = {
  pending: "等待中", running: "进行中", finished: "已结束", failed: "异常终止", stopped: "已停止",
};

export default function ArenaPage() {
  const { id } = useParams();
  const debateId = id ? Number(id) : undefined;
  const { data: detail, isError, error } = useQuery({
    queryKey: ["debate", debateId],
    queryFn: () => getDebate(debateId!),
    enabled: debateId !== undefined,
    refetchInterval: (query) =>
      ["finished", "failed", "stopped"].includes(query.state.data?.status ?? "") ? false : 5000,
  });
  useDebateStream(debateId);

  const status = useDebateStore((s) => s.status);
  const storeError = useDebateStore((s) => s.error);
  const phase = useDebateStore((s) => s.phase);
  const phaseName = useDebateStore((s) => s.phaseName);
  const budgetWords = useDebateStore((s) => s.budgetWords);
  const speeches = useDebateStore((s) => s.speeches);
  const timer = useDebateStore((s) => s.timer);
  const judging = useDebateStore((s) => s.judging);
  const liveVerdict = useDebateStore((s) => s.verdict);
  const streaming = useDebateStore((s) => s.streaming);
  const connected = useDebateStore((s) => s.connected);
  const bottomRef = useRef<HTMLDivElement>(null);

  // 归档回看场景：verdict 事件不参与重放，用详情接口数据兜底构造（CONSTRAINTS B5：DB 为唯一事实来源）
  const derivedVerdict: Verdict | null = useMemo(() => {
    if (!detail || detail.status !== "finished") return null;
    const votes = { pro: detail.votes?.pro ?? 0, con: detail.votes?.con ?? 0 };
    const totals = {
      pro: Number(detail.team_totals.pro ?? 0),
      con: Number(detail.team_totals.con ?? 0),
    };
    const decisive_rule = votes.pro !== votes.con
      ? "majority"
      : totals.pro !== totals.con
        ? "team_total"
        : detail.winner ? "best_speaker" : "unresolved";
    return {
      votes, team_totals: totals, winner: detail.winner,
      best_speaker: detail.best_speaker, decisive_rule,
      scorecards: detail.scorecards ?? [],
    };
  }, [detail]);
  const verdict = liveVerdict ?? derivedVerdict;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [speeches.length]);

  const format = detail?.format ?? "full";
  const running = status === "running";
  const terminal = status !== null && ["finished", "failed", "stopped"].includes(status);

  if (isError && (error as { response?: { status?: number } })?.response?.status === 404) {
    return (
      <div style={{ padding: 24, maxWidth: 960, margin: "0 auto" }}>
        <div className="neu-raised" style={{ padding: 24 }}>
          <div style={{ color: "var(--con-deep)", fontWeight: 700 }}>比赛不存在或不可见</div>
          <div style={{ color: "var(--ink-2)", fontSize: 14, margin: "6px 0 14px" }}>
            它可能属于其他用户，或已被删除。
          </div>
          <Button href="/debates">返回归档</Button>
        </div>
      </div>
    );
  }

  // VS 横幅两侧命题：从辩题标题的 “A / B” 结构切分
  const [proSide = "正方", conSide = "反方"] = (detail?.topic.title ?? "").split("/").map((s) => s.trim());

  // 动态计时条：当前环节已消耗字数（phase 戳由 store 在实况/回放时统一标注）
  const consumed = useMemo(
    () => speeches.filter((s) => s.phase === phase && s.camp !== "neutral").reduce((acc, s) => acc + (s.word_count ?? 0), 0),
    [speeches, phase],
  );
  return (
    <div style={{ padding: 24, maxWidth: 960, margin: "0 auto" }}>
      <div style={{ marginBottom: 14, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ margin: 0 }}>{detail?.topic.title ?? "…"}</h2>
        <Space>
          <Tag>{STATUS_CN[status ?? ""] ?? "…"}</Tag>
          {running && (
            <Tag style={{ color: connected ? "var(--accent-deep)" : "var(--con-deep)" }}>
              {connected && <span className="status-dot" />}
              {connected ? "直播中" : "轮询中"}
            </Tag>
          )}
          {running && (
            <button className="neu-btn" style={{ padding: "4px 12px", fontSize: 13, color: "var(--con-deep)" }} onClick={() => void stopDebate(debateId!)}>终止比赛</button>
          )}
          {status === "finished" && (
            <>
              <a className="neu-btn" style={{ padding: "4px 12px", fontSize: 13, textDecoration: "none", color: "var(--ink)" }} href={`/api/debates/${debateId}/export?format=md`} target="_blank">导出实录 (MD)</a>
              <a className="neu-btn" style={{ padding: "4px 12px", fontSize: 13, textDecoration: "none", color: "var(--ink)" }} href={`/api/debates/${debateId}/export?format=html`} target="_blank">导出报告 (HTML)</a>
            </>
          )}
          <a className="neu-btn" style={{ padding: "4px 12px", fontSize: 13, textDecoration: "none", color: "var(--ink)" }} href="/debates">归档</a>
        </Space>
      </div>

      <div className="vs-banner">
        <div className="vs-side pro">
          <div className="side-label">正 方</div>
          <div className="side-text">{proSide}</div>
        </div>
        <div className="vs-divider">
          {running && <span className="live-dot" />}
          VS
        </div>
        <div className="vs-side con">
          <div className="side-label">反 方</div>
          <div className="side-text">{conSide}</div>
        </div>
      </div>

      <PhaseTimeline format={format} currentPhase={phase} />
      <div style={{ height: 14 }} />

      {running && budgetWords !== null && budgetWords > 0 && phaseName !== "评委点评" && (
        <TimerBar consumed={consumed} budget={budgetWords} />
      )}
      {timer && running && (
        <div className={`neu-chip ${timer.type === "time_up" ? "danger" : "info"}`}>
          {timer.type === "time_up" ? "时间到，请停止发言" : "还剩 30 秒"}
        </div>
      )}
      {judging && running && <div className="neu-chip info">评委合议与点评中……</div>}
      {storeError && status === "failed" && <div className="neu-chip danger">{storeError}</div>}
      {verdict && <VerdictBanner verdict={verdict} />}
      {terminal && status === "finished" && detail?.usage && (
        <p style={{ color: "#888" }}>
          LLM 用量：{detail.usage.calls ?? 0} 次调用 / {(detail.usage.prompt_tokens ?? 0) + (detail.usage.completion_tokens ?? 0)} tokens
        </p>
      )}

      {speeches.map((speech) => (
        <SpeechCard key={speech.seq} speech={speech} />
      ))}
      {running && streaming && (
        <StreamingCard speaker={streaming.speaker} camp={streaming.camp} text={streaming.text} />
      )}
      <div ref={bottomRef} />

      {terminal && verdict && <ScoreBoard verdict={verdict} />}
    </div>
  );
}
