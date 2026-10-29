import { useQuery } from "@tanstack/react-query";
import { App as AntApp, Button, Popconfirm, Space, Tabs, Tag } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { deleteDebate, getDebate, stopDebate } from "../api/client";
import type { Verdict } from "../api/types";
import { FallacyReport } from "../components/FallacyReport";
import { FrameworkPanel } from "../components/FrameworkPanel";
import { JudgeReasonCards } from "../components/JudgeReasonCards";
import { PhaseTimeline } from "../components/PhaseTimeline";
import { ScoreBoard } from "../components/ScoreBoard";
import { SpeechCard } from "../components/SpeechCard";
import { StreamingCard } from "../components/StreamingCard";
import { TimerBar } from "../components/TimerBar";
import { VerdictBanner } from "../components/VerdictBanner";
import { SwimlaneSummary } from "../components/SwimlaneSummary";
import { useDebateStream } from "../hooks/useDebateStream";
import { useDebateStore } from "../stores/debateStore";

const STATUS_CN: Record<string, string> = {
  pending: "等待中", running: "进行中", finished: "已结束", failed: "异常终止", stopped: "已停止",
};

export default function ArenaPage() {
  const { id } = useParams();
  const debateId = id ? Number(id) : undefined;
  const { message } = AntApp.useApp();
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
  const judgingProgress = useDebateStore((s) => s.judgingProgress);
  const liveVerdict = useDebateStore((s) => s.verdict);
  const streaming = useDebateStore((s) => s.streaming);
  const connected = useDebateStore((s) => s.connected);
  const liveSwimlane = useDebateStore((s) => s.swimlane);
  const fallaciesBySeq = useDebateStore((s) => s.fallaciesBySeq);
  const fallacyCount = useMemo(
    () => Object.values(fallaciesBySeq).reduce((acc, arr) => acc + arr.length, 0),
    [fallaciesBySeq],
  );

  const [activeTab, setActiveTab] = useState("framework");
  const [showJump, setShowJump] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  // 归档回看场景：verdict 事件不参与重放，用详情接口数据兜底构造（CONSTRAINTS B5：DB 为唯一事实来源）
  const derivedVerdict: Verdict | null = useMemo(() => {
    if (!detail || detail.status !== "finished") return null;
    const votes = { pro: detail.votes?.pro ?? 0, con: detail.votes?.con ?? 0 };
    const totals = {
      pro: Number(detail.team_totals.pro ?? 0),
      con: Number(detail.team_totals.con ?? 0),
    };
    // 决胜依据由后端按落库元数据给出（含 final_judge 智能终裁）；历史场次走同一优先级回算
    const decisive_rule = detail.decisive_rule
      ?? (votes.pro !== votes.con
        ? "majority"
        : totals.pro !== totals.con
          ? "team_total"
          : detail.winner ? "best_speaker" : "unresolved");
    return {
      votes, team_totals: totals, winner: detail.winner,
      best_speaker: detail.best_speaker, decisive_rule,
      final_reason: detail.final_reason ?? null,
      scorecards: detail.scorecards ?? [],
    };
  }, [detail]);
  const verdict = liveVerdict ?? derivedVerdict;

  // 滚动体验（辩论平台大坑规避）：绝不强制自动滚到底。
  // 「回到最新发言」浮动按钮由用户手动触发，仅在发言区未到底且不在视野内时出现。
  useEffect(() => {
    const el = bottomRef.current;
    if (!el || activeTab !== "record") {
      setShowJump(false);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => setShowJump(!entry.isIntersecting),
      { rootMargin: "120px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [activeTab]);

  const format = detail?.format ?? "full";
  const running = status === "running";
  const terminal = status !== null && ["finished", "failed", "stopped"].includes(status);

  // 评审进度提示（v1.9.2）：后端每 10s 推一次心跳，显示当前阶段与已用时——
  // 用户能看出"还在跑"还是"真的卡了"，不再只看到一个静止的"评委合议中……"
  const judgingChip = judging && running ? (
    <div className="neu-chip info" data-testid="judging-progress">
      ⚖️{" "}
      {judgingProgress
        ? `${judgingProgress.stage === "评审"
          ? `评委评审中（${judgingProgress.completed}/${judgingProgress.total} 位已出评分）`
          : judgingProgress.stage === "质检"
            ? "机器论证质检中"
            : judgingProgress.stage} · 已用时 ${Math.round(judgingProgress.elapsed)}s`
        : "评委合议与点评中……"}
    </div>
  ) : null;

  if (isError && (error as { response?: { status?: number } })?.response?.status === 404) {
    return (
      <div style={{ maxWidth: 1080 }}>
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

  // 动态计时条：当前环节已消耗字数（phase 戳由 store 在实况/回放时统一标注）
  const consumed = useMemo(
    () => speeches.filter((s) => s.phase === phase && s.camp !== "neutral").reduce((acc, s) => acc + (s.word_count ?? 0), 0),
    [speeches, phase],
  );
  const timeUp = running && timer?.type === "time_up";
  const showTimer = running && budgetWords !== null && budgetWords > 0 && phaseName !== "评委点评";

  // 自由辩轮次指示（UI 约束辅助）：上一个发言方的下一轮归对方，禁止连续发言
  const lastCamp = useMemo(
    () => [...speeches].reverse().find((s) => s.camp !== "neutral")?.camp,
    [speeches],
  );
  const nextCamp = lastCamp === "pro" ? "正方" : "反方";
  const inCross = running && (phaseName?.includes("质询") || phaseName?.includes("攻辩"));
  const inFree = running && phase === 7;
  const latestSeq = speeches.length ? speeches[speeches.length - 1].seq : -1;

  const recordTab = (
    <div>
      <PhaseTimeline format={format} currentPhase={phase} />
      <div style={{ height: 14 }} />

      {showTimer && <TimerBar consumed={consumed} budget={budgetWords!} timeUp={timeUp} />}
      {timer && running && (
        <div className={`neu-chip ${timer.type === "time_up" ? "danger" : "info"}`}>
          {timer.type === "time_up" ? "⏱ 时间到，请停止发言" : "⏱ 还剩 30 秒"}
        </div>
      )}
      {/* 赛制规则可视化提示（辅助 Agent 行为约束） */}
      {inCross && <div className="neu-chip info">🛡 质询环节：质询方仅可提问 ｜ 被质询方禁止反问</div>}
      {inFree && (
        <div className={`neu-chip ${lastCamp === "pro" ? "info" : "danger"}`}>
          🎙 自由辩论：当前轮到 {nextCamp} 发言（禁止连续发言）
        </div>
      )}
      {judgingChip}
      {storeError && status === "failed" && <div className="neu-chip danger">{storeError}</div>}

      {speeches.map((speech) => (
        <div key={speech.seq} id={`speech-${speech.seq}`} style={{ scrollMarginTop: 150 }}>
          <SpeechCard speech={speech} active={running && speech.seq === latestSeq} />
        </div>
      ))}
      {running && streaming && (
        <StreamingCard speaker={streaming.speaker} camp={streaming.camp} text={streaming.text} />
      )}
      <div ref={bottomRef} />
    </div>
  );

  const items = [
    {
      key: "framework",
      label: "📌 立论框架",
      children: detail ? (
        <FrameworkPanel
          detail={detail}
          live={running}
          chairSpeeches={speeches.filter(
            (s) => s.speaker === "chair" && (s.phase === 1 || s.phase_name === "主席开场"),
          )}
        />
      ) : null,
    },
    { key: "record", label: `💬 辩论记录${speeches.length ? `（${speeches.length}）` : ""}`, children: recordTab },
    {
      // v2.7 复盘泳道总结：直播随 SSE 实时生长，归档用详情接口现算的兜底
      key: "swimlane",
      label: "📋 复盘总结",
      children: <SwimlaneSummary payload={liveSwimlane ?? detail?.swimlane ?? null} />,
    },
    { key: "qc", label: `🧐 质检报告${fallacyCount ? `（${fallacyCount}）` : ""}`, children: <FallacyReport speeches={speeches} /> },
    { key: "judge", label: "⭐ 评委打分", children: (
      <div>
        {judgingChip}
        {status === "failed" && storeError && (
          <div className="neu-chip danger" data-testid="judging-failed">
            评审未能完成：{storeError}
          </div>
        )}
        {verdict && <VerdictBanner verdict={verdict} />}
        {verdict?.decisive_rule === "final_judge" && (
          <div className="neu-chip info" data-testid="final-judge-note">
            ⚖️ 评委投票与团体总分持平，本场由评议长智能终裁决出胜方
            {verdict.final_reason ? `：${verdict.final_reason}` : "（终裁理由见宣判词）"}
          </div>
        )}
        {verdict && (
          <>
            <h4 className="tab-sub">为什么这样打分 · 评委理由</h4>
            <JudgeReasonCards cards={verdict.scorecards ?? []} />
            {terminal && (
              <>
                <h4 className="tab-sub">评分明细（点击评委行展开分维依据）</h4>
                <ScoreBoard verdict={verdict} />
              </>
            )}
          </>
        )}
        {!verdict && !judging && (
          <div className="neu-chip">评分将在评审环节结束后展示（进行中可先看「质检报告」与「辩论记录」）。</div>
        )}
      </div>
    ) },
    { key: "export", label: "📄 导出文稿", children: (
      <div>
        <div className="neu-raised export-panel">
          <div className="fw-block-label">赛后导出</div>
          <Space wrap style={{ marginTop: 12 }}>
            <a className="neu-btn" style={{ padding: "8px 18px", fontSize: 13, textDecoration: "none", color: "var(--ink)", pointerEvents: status === "finished" ? "auto" : "none", opacity: status === "finished" ? 1 : 0.5 }} href={`/api/debates/${debateId}/export?format=md`} target="_blank">导出实录 (Markdown)</a>
            <a className="neu-btn" style={{ padding: "8px 18px", fontSize: 13, textDecoration: "none", color: "var(--ink)", pointerEvents: status === "finished" ? "auto" : "none", opacity: status === "finished" ? 1 : 0.5 }} href={`/api/debates/${debateId}/export?format=html`} target="_blank">导出报告 (HTML)</a>
            <a className="neu-btn" style={{ padding: "8px 18px", fontSize: 13, textDecoration: "none", color: "var(--ink)", pointerEvents: status === "finished" ? "auto" : "none", opacity: status === "finished" ? 1 : 0.5 }} href={`/api/debates/${debateId}/export?format=xlsx`} target="_blank">导出评分 (XLSX)</a>
          </Space>
          {status !== "finished" && <p style={{ color: "var(--ink-3)", fontSize: 12, marginTop: 10 }}>比赛结束后可导出。</p>}
        </div>
        {terminal && status === "finished" && detail?.usage && (
          <p style={{ color: "var(--ink-3)" }}>
            LLM 用量：{detail.usage.calls ?? 0} 次调用 / {(detail.usage.prompt_tokens ?? 0) + (detail.usage.completion_tokens ?? 0)} tokens
          </p>
        )}
      </div>
    ) },
  ];

  return (
    <div style={{ maxWidth: 1080 }}>
      {/* 置顶标题栏：辩题 + 状态 + 常驻计时，始终可见不随聊天滚动消失 */}
      <div className="arena-sticky">
        <div className="arena-sticky-top">
          <h2 className="ws-title" style={{ margin: 0, minWidth: 0 }}>
            <span className="arena-title-text">{detail?.topic.title ?? "…"}</span>
          </h2>
          <div className="ws-actions">
            <Space size={6} wrap>
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
              {terminal && debateId !== undefined && (
                <Popconfirm
                  title="删除本场归档？"
                  description="发言与评分卡会一并删除，不可恢复。"
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={async () => {
                    try {
                      await deleteDebate(debateId);
                      message.success("已删除本场归档");
                      window.location.href = "/debates";
                    } catch (err) {
                      const body = (err as { response?: { data?: { message?: string } } })?.response?.data;
                      message.error(body?.message || "删除失败，请稍后重试");
                    }
                  }}
                >
                  <button className="neu-btn" style={{ padding: "4px 12px", fontSize: 13, color: "var(--con-deep)" }}>删除本场</button>
                </Popconfirm>
              )}
              <a className="neu-btn" style={{ padding: "4px 12px", fontSize: 13, textDecoration: "none", color: "var(--ink)" }} href="/debates">归档</a>
            </Space>
          </div>
        </div>
        {/* 常驻计时：当前环节｜已用｜剩余；超时高亮变红 */}
        <div className={`arena-clock ${timeUp ? "over" : ""}`} data-testid="arena-clock">
          <span className="clock-phase">当前环节：{phaseName ?? (status === "finished" ? "已宣判" : "待开始")}</span>
          {showTimer && (
            <>
              <span className="clock-item">已用 ≈{Math.round((consumed / 220) * 60)} 秒</span>
              <span className={`clock-item ${timeUp ? "clock-over" : ""}`}>{timeUp ? "已超时" : `剩余 ≈${Math.round((Math.max(0, budgetWords! - consumed) / 220) * 60)} 秒`}</span>
              <span className="clock-item">字数 {consumed}/{budgetWords}</span>
            </>
          )}
          {timer && running && (
            <span className={`clock-item ${timeUp ? "clock-over" : ""}`}>
              {timer.type === "time_up" ? "⏱ 时间到" : "⏱ 还剩 30 秒"}
            </span>
          )}
        </div>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={items}
        className="arena-tabs"
        destroyOnHidden={false}
      />

      {showJump && activeTab === "record" && (
        <button
          className="neu-btn-accent jump-latest"
          onClick={() => bottomRef.current?.scrollIntoView({ behavior: "smooth" })}
        >
          ↓ 回到最新发言
        </button>
      )}
    </div>
  );
}
