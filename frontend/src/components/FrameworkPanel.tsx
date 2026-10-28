import { useState } from "react";
import type { DebateDetail, LiveSpeech } from "../api/types";
import { RichText } from "./RichText";
import { roleDisplay } from "./SpeechCard";

const CN_CIRCLED = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧"];

/** 主席开篇演讲：第一段是「引题演讲」（正式开赛前的观众导览），其后为流程报幕（宣布比赛开始）。 */
function ChairKeynote({ speeches, live }: { speeches: LiveSpeech[]; live: boolean }) {
  const [openSeq, setOpenSeq] = useState<number | null>(null);
  if (speeches.length === 0) {
    return (
      <div className="fw-keynote empty" data-testid="chair-keynote">
        <span className="fw-keynote-tag">🎙 主席开篇</span>
        <p className="fw-empty">
          {live
            ? "主席正在准备开篇演讲（辩题引题 → 报幕开赛），稍候片刻即可看到。"
            : "（该场次未记录主席开篇：属升级前的旧场次，重新开赛即可看到引题演讲与结尾致辞）"}
        </p>
      </div>
    );
  }
  return (
    <div className="fw-keynote" data-testid="chair-keynote">
      <div className="fw-keynote-head">
        <span className="fw-keynote-title">🎙 主席 · 开篇演讲</span>
        <span className="fw-keynote-tag">中立 · 不站边</span>
        <span className="fw-keynote-meta">开赛前导览 · 共 {speeches.length} 段</span>
      </div>
      {speeches.map((s, i) => {
        const isTeaser = speeches.length > 1 && i === 0;
        const long = (s.text ?? "").length > 240;
        const open = openSeq === s.seq;
        return (
          <div key={s.seq} className={`fw-keynote-seg${isTeaser ? " teaser" : ""}`}>
            <div className="fw-keynote-seg-head">
              <span className="fw-chair-role">{roleDisplay(s.speaker)}</span>
              <span className="fw-chair-tag">{isTeaser ? "引题演讲 · 带你入场" : "开场报幕"}</span>
              <span className="fw-open-meta">#{s.seq}{s.word_count ? ` · ${s.word_count} 字` : ""}</span>
            </div>
            <p className={`fw-keynote-text${long && !open ? " is-clamped" : ""}`}>
              <RichText text={s.text} />
            </p>
            {long && (
              <button className="fw-open-toggle" onClick={() => setOpenSeq(open ? null : s.seq)}>
                {open ? "收起" : "展开全文"}
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

function SidePanel({
  side,
  stance,
  points,
}: {
  side: "pro" | "con";
  stance?: string;
  points: string[];
}) {
  const label = side === "pro" ? "正 方" : "反 方";
  return (
    <div className={`fw-panel ${side}`} data-testid={`framework-${side}`}>
      <div className="fw-side-label">{label}</div>
      <div className="fw-stance">
        <span className="fw-block-label">定义 · 立场</span>
        <p>{stance || "（未提供，以辩题表述为准）"}</p>
      </div>
      <div className="fw-block">
        <span className="fw-block-label">核心论点</span>
        {points.length === 0 ? (
          <p className="fw-empty">（暂无破题要点）</p>
        ) : (
          <ol className="fw-points">
            {points.slice(0, CN_CIRCLED.length).map((p, i) => (
              <li key={i} className="fw-point">
                <span className={`fw-num num-${side}`}>{CN_CIRCLED[i]}</span>
                <span className="fw-point-text">{p}</span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

/**
 * 立场解析（v2.3）：优先 stance 字段；剔除旧版派生的「支持：」前缀与"整句辩题"垃圾值；
 * stance 不可用时回落到用户填写的破题要点首行（该行随之从论点列表中移除，避免重复展示）。
 */
function resolveSide(
  stance: string | undefined,
  points: string[],
  fallback: string,
): { stance: string; points: string[] } {
  const cleaned = (stance ?? "").trim().replace(/^支持[:：]\s*/, "");
  if (cleaned && cleaned !== fallback) {
    return { stance: cleaned, points };
  }
  const head = points[0];
  if (head && head.length <= 40) {
    return { stance: head, points: points.slice(1) };
  }
  return { stance: head || fallback, points: points.slice(head ? 1 : 0) };
}

/**
 * 立论框架（常驻观点区）：中立主席的开篇演讲（报幕 + 引题）+ 双方定义/判准/分论点，
 * 正方蓝 / 反方红区分色，论点带 ①②③ 编号——评委看攻防时可快速对应“对方在打正方论点 2”。
 * 演讲由主席承担（中立、不站边），辩手不在此区发言。
 */
export function FrameworkPanel({
  detail,
  chairSpeeches = [],
  live = false,
}: {
  detail: DebateDetail;
  chairSpeeches?: LiveSpeech[];
  live?: boolean;
}) {
  const title = detail.topic.title;
  const [proSide = "正方", conSide = "反方"] = title.split("/").map((s) => s.trim());
  // v2.3：横幅与下方面板共用同一套立场解析——优先用户定义的立场字段，
  // 垃圾值（旧版"支持：整句辩题"）回落到用户填写的破题要点首行。
  const pro = resolveSide(detail.topic.pro_stance, detail.topic.pro_points ?? [], proSide);
  const con = resolveSide(detail.topic.con_stance, detail.topic.con_points ?? [], conSide);
  return (
    <div>
      <div className="vs-banner" data-testid="vs-banner">
        <div className="vs-side pro">
          <div className="side-head">
            <span className="side-badge pro-badge">正方</span>
          </div>
          <div className="side-stance">{pro.stance}</div>
        </div>
        <div className="vs-divider">VS</div>
        <div className="vs-side con">
          <div className="side-head">
            <span className="side-badge con-badge">反方</span>
          </div>
          <div className="side-stance">{con.stance}</div>
        </div>
      </div>
      <ChairKeynote speeches={chairSpeeches} live={live} />
      <div className="fw-grid">
        <SidePanel side="pro" stance={pro.stance} points={pro.points} />
        <SidePanel side="con" stance={con.stance} points={con.points} />
      </div>
      <p className="fw-hint">对照提示：主席开篇只交代争议与看点、不站边；双方定义与论点逐条列出，攻防时可结合论点编号快速定位交锋点，谬误标注挂在具体发言上。</p>
    </div>
  );
}
