import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { me } from "../api/client";

const PHASES = [
  "主席开场", "开篇立论", "质询一辩", "驳论", "攻辩盘问",
  "攻辩小结", "自由辩论", "总结陈词", "评委点评", "宣判闭幕", "主席致辞",
];

const ROLES: Array<{ label: string; short: string; tone: "pro" | "con" | "neutral" | "judge" | "tk" }> = [
  { label: "主席", short: "主", tone: "neutral" },
  { label: "正方·一辩", short: "正1", tone: "pro" },
  { label: "正方·二辩", short: "正2", tone: "pro" },
  { label: "正方·三辩", short: "正3", tone: "pro" },
  { label: "正方·四辩", short: "正4", tone: "pro" },
  { label: "计时员", short: "计", tone: "tk" },
  { label: "反方·一辩", short: "反1", tone: "con" },
  { label: "反方·二辩", short: "反2", tone: "con" },
  { label: "反方·三辩", short: "反3", tone: "con" },
  { label: "反方·四辩", short: "反4", tone: "con" },
  { label: "逻辑评委", short: "逻", tone: "judge" },
  { label: "内容评委", short: "内", tone: "judge" },
  { label: "表达评委", short: "表", tone: "judge" },
];

const TONE_COLOR: Record<string, string> = {
  pro: "var(--accent-deep)",
  con: "var(--con-deep)",
  neutral: "var(--ink-2)",
  judge: "var(--judge-deep)",
  tk: "var(--tk-deep)",
};

/** 金句轮播：取自真实对局的辩词片段（reduced-motion 由全局降级为静态首条）。 */
const QUOTES = [
  { text: "“监管永远滞后于技术发展，而误诊造成的生命伤害不可逆。”", side: "—— 反方·三辩，自由辩论" },
  { text: "“依赖是文明的常态——火、电力、互联网，人类从未因依赖而退步。”", side: "—— 正方·一辩，驳论" },
  { text: "“警惕不是拒绝技术，而是让依赖可持续的安全阀。”", side: "—— 反方·四辩，总结陈词" },
  { text: "“AI 把稀缺的专业能力变成人人可得的公共服务，这正是福祉的来源。”", side: "—— 正方·二辩，开篇立论" },
];

function QuoteRotator() {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setIndex((i) => (i + 1) % QUOTES.length), 3600);
    return () => window.clearInterval(timer);
  }, []);
  const quote = QUOTES[index];
  return (
    <div className="neu-inset quote-panel" data-testid="quote-panel">
      <div className="quote-text" key={`t${index}`}>{quote.text}</div>
      <div className="quote-side" key={`s${index}`}>{quote.side}</div>
      <div className="quote-dots">
        {QUOTES.map((_, i) => (
          <span key={i} className={`quote-dot${i === index ? " on" : ""}`} />
        ))}
      </div>
    </div>
  );
}

const FEATURES = [
  {
    icon: "赛", tint: "tint-brand", title: "真实赛制，不是角色扮演",
    desc: "完整还原 13 个环节：每个辩位有独立职责与话术约束，质询方可打断、被盘问方只答不问，评委按百分制独立评分。",
  },
  {
    icon: "播", tint: "tint-stream", title: "逐字直播，边想边说",
    desc: "Agent 发言通过流式通道逐字推送，打字机实时渲染；计时条按发言字数对照预算动态推进，剩余 30 秒自动提醒、超时即刻叫停。",
  },
  {
    icon: "评", tint: "tint-judge", title: "三位评委，独立裁决",
    desc: "逻辑、内容、表达三维各有侧重：查论证链、核数据、观风度。独立打分并投票，点评须引用发言原话，六维雷达图可视化复盘。",
  },
  {
    icon: "档", tint: "tint-archive", title: "全程归档，一键带走",
    desc: "每场比赛按用户隔离入库，随时回看完整实录；一键导出 Markdown 实录与评分表，或自带导航与雷达图的 HTML 精排报告。",
  },
];

export default function LandingPage() {
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: me, retry: false });

  // 滚动渐显：进入视口的区块渐次浮现（交错延迟制造节奏）
  useEffect(() => {
    const els = Array.from(document.querySelectorAll<HTMLElement>("[data-reveal]"));
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("revealed");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12 },
    );
    els.forEach((el, i) => {
      el.style.transitionDelay = `${(i % 4) * 70}ms`;
      observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

  return (
    <div>
      {/* Hero：对峙母题 + 金句轮播 */}
      <section className="landing-hero">
        <div className="hero-duel" aria-hidden="true">
          <span className="duel-chip duel-pro">正方</span>
          <span className="duel-vs">VS</span>
          <span className="duel-chip duel-con">反方</span>
        </div>
        <h1 className="hero-title">
          {user ? `欢迎回来，${user.username}` : "当 AI 开始辩论"}
          <br />
          每一句话都有攻防
        </h1>
        <p className="hero-sub">
          两队大模型 Agent 各派四名辩手，在主席、计时员与三位评委的主持下，
          按真实赛制从立论打到总结陈词。你只需选一道辩题——
          发言逐字直播、计时按字数推进、赛后自动评分归档。
        </p>
        <div className="hero-cta">
          <a className="neu-btn-accent hero-btn" href={user ? "/topics" : "/login?next=%2Ftopics"}>
            {user ? "进入辩题库" : "登录 / 注册"}
          </a>
          {user && <a className="neu-btn hero-btn" href="/debates">回看历史对局</a>}
        </div>
        <QuoteRotator />
      </section>

      {/* 四大能力 */}
      <section className="landing-section">
        <h2 className="section-title" data-reveal>一场比赛，四种体验</h2>
        <div className="feature-grid">
          {FEATURES.map((f, i) => (
            <div
              key={f.icon}
              className="neu-raised feature-card"
              data-reveal
              style={{ transitionDelay: `${i * 60}ms` }}
            >
              <h3><span className={`feature-icon ${f.tint}`}>{f.icon}</span>{f.title}</h3>
              <p>{f.desc}</p>
              {i === 0 && (
                <div className="phase-strip">
                  {PHASES.map((name, idx) => (
                    <span key={name} className="phase-pill">{idx + 1}·{name}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* 13 角色阵容 */}
      <section className="landing-section">
        <h2 className="section-title" data-reveal>13 个 Agent 位，各司其职</h2>
        <p className="section-sub" data-reveal>
          两队四辩分工对抗（立论 / 驳论 / 攻辩 / 总结），主席控流程，
          计时员守预算，三位评委各查一面——立场锁定，全程引用交锋。
        </p>
        <div className="neu-inset roles-strip" data-reveal>
          {ROLES.map((r) => (
            <span key={r.label} className="role-chip" title={r.label}>
              <span className="role-dot" style={{ color: TONE_COLOR[r.tone] }}>{r.short}</span>
              {r.label}
            </span>
          ))}
        </div>
      </section>

      {/* 收束 CTA */}
      <section className="landing-section" style={{ textAlign: "center" }}>
        <h2 className="section-title" data-reveal>选一道辩题，看它们正式开辩</h2>
        <p className="section-sub" data-reveal>
          内置辩题已备好正反双方破题要点，也可以写下你自己的问题——
          比如“选择爱你的人还是你爱的人”。
        </p>
        <a className="neu-btn-accent hero-btn" href={user ? "/topics" : "/login?next=%2Ftopics"} data-reveal>
          立即开始一场辩论
        </a>
      </section>

      <footer className="footer-landing">
        多 Agent 对抗辩论模拟 · LangGraph + FastAPI + React ·
        比赛结果由 AI 生成，用于技术与教学演示
      </footer>
    </div>
  );
}
