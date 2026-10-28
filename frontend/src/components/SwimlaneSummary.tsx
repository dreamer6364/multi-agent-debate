import { Tag } from "antd";
import type { SwimlaneEntry, SwimlanePayload } from "../api/types";

/**
 * 复盘泳道总结（v2.7）：阶段 × 泳道（正方 / 反方 / 公共场域）。
 *
 * 数据由后端按实录与图状态现算（指向/被指向与论证关系图同源），
 * 本组件只负责把每行的两个阵营条目与公共标注排版成可读的泳道卡。
 */

const CAMP_META = {
  pro: { title: "正方阵营", className: "sl-camp-pro" },
  con: { title: "反方阵营", className: "sl-camp-con" },
} as const;

function EntryCard({ entry, side }: { entry: SwimlaneEntry; side: "pro" | "con" }) {
  const victim = entry.action === "被指向";
  return (
    <div className={`sl-entry ${victim ? "sl-victim" : ""}`} data-testid="swimlane-entry">
      <div className="sl-entry-head">
        <span className={`sl-speaker ${CAMP_META[side].className}`}>{entry.speaker}</span>
        <span className={`sl-action ${victim ? "sl-action-hit" : ""}`}>{entry.action}</span>
      </div>
      <p className="sl-content">{entry.content}</p>
      {(entry.points_to.length > 0 || entry.pointed_by.length > 0) && (
        <div className="sl-refs">
          {entry.points_to.map((p, i) => (
            <Tag key={`pt-${i}`} className="sl-tag sl-tag-point" title={`指向：${p}`}>
              → {p}
            </Tag>
          ))}
          {entry.pointed_by.map((p, i) => (
            <Tag key={`pb-${i}`} className="sl-tag sl-tag-pointed" title={`被指向：${p}`}>
              ← {p}
            </Tag>
          ))}
        </div>
      )}
    </div>
  );
}

function CampCell({ entries, side }: { entries: SwimlaneEntry[]; side: "pro" | "con" }) {
  if (!entries.length) {
    return <span className="sl-none">—</span>;
  }
  return (
    <>
      {entries.map((entry, i) => (
        <EntryCard key={`${entry.speaker}-${entry.seq ?? i}-${i}`} entry={entry} side={side} />
      ))}
    </>
  );
}

export function SwimlaneSummary({ payload }: { payload: SwimlanePayload | null }) {
  if (!payload || !payload.rows.length) {
    return (
      <div className="sl-empty" data-testid="swimlane-empty">
        <p className="sl-empty-title">📋 复盘总结随辩论逐步生成</p>
        <p className="sl-empty-sub">
          每条发言落定后，这里会按「阶段 × 阵营泳道」汇总攻防结构：谁在什么环节做了什么、指向谁、被谁指向。
        </p>
      </div>
    );
  }

  return (
    <div className="sl" data-testid="swimlane-summary">
      {payload.rows.map((row) => (
        <section className="sl-row" key={row.phase} data-testid="swimlane-row">
          <div className="sl-phase">
            <span className="sl-phase-name">{row.name}</span>
          </div>
          <div className="sl-cell" data-testid="swimlane-pro">
            <div className="sl-cell-title pro">{CAMP_META.pro.title}</div>
            <CampCell entries={row.pro} side="pro" />
          </div>
          <div className="sl-cell" data-testid="swimlane-con">
            <div className="sl-cell-title con">{CAMP_META.con.title}</div>
            <CampCell entries={row.con} side="con" />
          </div>
          <div className="sl-cell sl-public" data-testid="swimlane-public">
            <div className="sl-cell-title neutral">公共场域</div>
            <div className="sl-public-chips">
              <Tag className="sl-tag">接收方：{row.public.receivers}</Tag>
              <Tag className="sl-tag">计时员：{row.public.timer}</Tag>
            </div>
            {row.public.notes.length > 0 && (
              <ul className="sl-notes">
                {row.public.notes.map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
              </ul>
            )}
          </div>
        </section>
      ))}
      <p className="sl-note">
        总结由后端按比赛实录与论证关系实时推导：实线箭头＝本条指向的对象（含关系：反驳／质询／支持），
        虚线回指＝本条论点被谁攻击；公共场域汇总接收方、计时与违规／质检等特殊标注。
      </p>
    </div>
  );
}
