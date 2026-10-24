import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { me } from "../api/client";

const QUOTES = [
  { speaker: "正方·三辩", camp: "pro", text: "AI 误诊风险不能成为否定技术的理由——历史上医疗技术每一步都伴随风险，但总体挽救了更多生命。" },
  { speaker: "反方·三辩", camp: "con", text: "请正面回答：AI 误诊导致的患者伤害，能否通过监管完全避免？若不能，这种不可逆的代价，难道不值得警惕？" },
  { speaker: "正方·四辩", camp: "pro", text: "对方把“依赖”偷换为“沉迷”。我方主张的，是把技术深度纳入生产生活决策的常态机制。" },
  { speaker: "反方·四辩", camp: "con", text: "警惕不是对立面，而是安全阀——正是批判性审视，推动了更安全、更可靠的技术发展。" },
];

const FEATURES = [
  { title: "真实赛制", desc: "完整还原 13 个环节：独立辩位职责、质询打断权、只答不问、百分制评分——不是聊天机器人轮流说话。" },
  { title: "逐字直播", desc: "发言通过流式通道逐字推送，打字机实时渲染；计时条按字数预算推进，剩余 30 秒提醒、超时叫停。" },
  { title: "三位评委", desc: "逻辑、内容、表达三维独立打分，点评必须引用发言原话，六维雷达图可视化复盘。" },
  { title: "全程归档", desc: "按用户隔离入库，随时回看；一键导出 Markdown 实录、HTML 精排报告与 xlsx 评分表。" },
];

const ROLES: Array<{ label: string; short: string; camp: "pro" | "con" | "n" }> = [
  { label: "主席", short: "主", camp: "n" },
  { label: "正一辩", short: "正1", camp: "pro" },
  { label: "正二辩", short: "正2", camp: "pro" },
  { label: "正三辩", short: "正3", camp: "pro" },
  { label: "正四辩", short: "正4", camp: "pro" },
  { label: "计时员", short: "计", camp: "n" },
  { label: "反一辩", short: "反1", camp: "con" },
  { label: "反二辩", short: "反2", camp: "con" },
  { label: "反三辩", short: "反3", camp: "con" },
  { label: "反四辩", short: "反4", camp: "con" },
  { label: "逻辑评委", short: "逻", camp: "n" },
  { label: "内容评委", short: "内", camp: "n" },
  { label: "表达评委", short: "表", camp: "n" },
];

/** 字幕机：逐字打出真实对局辩词（reduced-motion 时静态完整显示）。 */
function TranscriptTicker() {
  const [index, setIndex] = useState(0);
  const [chars, setChars] = useState(0);
  const reduced = useRef(false);

  useEffect(() => {
    reduced.current = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let timer: number;
    const quote = QUOTES[index].text;
    if (reduced.current) {
      setChars(quote.length);
      timer = window.setTimeout(() => { setIndex((i) => (i + 1) % QUOTES.length); setChars(0); }, 3600);
    } else if (chars < quote.length) {
      timer = window.setTimeout(() => setChars((c) => c + 1), 55);
    } else {
      timer = window.setTimeout(() => { setIndex((i) => (i + 1) % QUOTES.length); setChars(0); }, 1800);
    }
    return () => window.clearTimeout(timer);
  }, [index, chars]);

  const quote = QUOTES[index];
  const shown = quote.text.slice(0, chars);
  return (
    <div className="ld-ticker" data-testid="transcript-ticker">
      <div className="ld-ticker-head">
        <span className={`ld-camp-tag ${quote.camp}`}>{quote.speaker}</span>
        <span className="ld-ticker-meta">LIVE TRANSCRIPT · 自由辩论</span>
      </div>
      <p className="ld-ticker-text">
        {shown}
        <span className="ld-caret" />
      </p>
      <div className="ld-ticker-dots">
        {QUOTES.map((_, i) => <span key={i} className={`ld-dot${i === index ? " on" : ""}`} />)}
      </div>
    </div>
  );
}

export default function LandingPage() {
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: me, retry: false });

  return (
    <div className="ld-page">
      <div className="ld-spot ld-spot-violet" aria-hidden="true" />
      <div className="ld-spot ld-spot-coral" aria-hidden="true" />

      <header className="ld-header">
        <div className="ld-brand">辩<span>多 Agent 对抗辩论模拟</span></div>
        <nav>
          <a href="/" className="ld-nav-on">开始</a>
          <a href="/topics">辩题库</a>
          <a href="/debates">归档</a>
          {user
            ? <a href="/topics" className="ld-user">{user.username} · 进入工作台</a>
            : <a href="/login" className="ld-user">登录 / 注册</a>}
        </nav>
      </header>

      <main>
        <section className="ld-hero">
          <p className="ld-eyebrow">MULTI-AGENT DEBATE · 13 ROLES · LIVE STREAMING</p>
          <h1 className="ld-title">
            两队 AI 正面交锋
            <br />
            <span className="ld-glow-violet">正方</span> 主张，
            <span className="ld-glow-coral">反方</span> 拆解
          </h1>
          <p className="ld-sub">
            13 个 Agent 位按真实辩论赛制对抗：立论、质询、攻辩、自由辩论、总结陈词。
            发言逐字直播，计时按字数推进，三位评委引用原话独立裁决——
            你只负责选一道辩题。
          </p>
          <div className="ld-cta">
            <a className="ld-btn ld-btn-primary" href={user ? "/topics" : "/login?next=%2Ftopics"}>
              {user ? "进入工作台" : "登录，开一场辩论"}
            </a>
            <a className="ld-btn ld-btn-ghost" href="/debates">看看别人辩过什么</a>
          </div>
          <TranscriptTicker />
        </section>

        <section className="ld-mock" aria-hidden="true">
          <div className="ld-mock-head">
            <span className="ld-mock-dot" /> <span className="ld-mock-dot" /> <span className="ld-mock-dot" />
            <span className="ld-mock-title">直播 · 自由辩论 · 剩余 30 秒</span>
          </div>
          <div className="ld-mock-line pro">
            <b>正方·三辩</b>风险不能靠“想想”消除——按对方逻辑，汽车有事故就不该上路？
          </div>
          <div className="ld-mock-line con">
            <b>反方·三辩</b>汽车有安全带与法规，AI 决策的黑箱有吗？请回答，而不是类比。
          </div>
          <div className="ld-mock-line pro">
            <b>正方·二辩</b>黑箱可解释性研究每年都在进步，这正是深度依赖推动的治理。
          </div>
          <div className="ld-mock-timer"><span style={{ width: "72%" }} /></div>
        </section>

        <section className="ld-features">
          {FEATURES.map((f) => (
            <article key={f.title} className="ld-feature">
              <h3>{f.title}</h3>
              <p>{f.desc}</p>
            </article>
          ))}
        </section>

        <section className="ld-cast">
          <h2>13 个 Agent 位，各司其职</h2>
          <div className="ld-cast-grid">
            {ROLES.map((r) => (
              <span key={r.label} className={`ld-role ld-role-${r.camp}`}>
                <b>{r.short}</b>{r.label}
              </span>
            ))}
          </div>
        </section>

        <section className="ld-final">
          <h2>选一道辩题，看它们正式开辩</h2>
          <p>内置辩题备好正反破题要点；也可以写下你自己的问题。</p>
          <a className="ld-btn ld-btn-primary" href={user ? "/topics" : "/login?next=%2Ftopics"}>立即开始一场辩论</a>
        </section>
      </main>

      <footer className="ld-footer">
        多 Agent 对抗辩论模拟 · LangGraph + FastAPI + React · 比赛结果由 AI 生成，用于技术与教学演示
      </footer>
    </div>
  );
}
