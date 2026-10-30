import { useQuery, useQueryClient } from "@tanstack/react-query";
import { App as AntApp, Popconfirm, Table, Tag } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { clearArchivedDebates, deleteDebate, getDebates } from "../api/client";
import type { DebateStatus } from "../api/types";

const STATUS_COLOR: Record<string, string> = {
  pending: "default",
  running: "processing",
  finished: "success",
  failed: "error",
  stopped: "warning",
};
const STATUS_CN: Record<DebateStatus, string> = {
  pending: "等待中",
  running: "进行中",
  finished: "已结束",
  failed: "异常终止",
  stopped: "已停止",
};

/** 取后端统一错误信封里的 message（errors.py：{code, message}），拿不到就返回兜底文案 */
function errorText(err: unknown, fallback: string): string {
  const body = (err as { response?: { data?: { message?: string } } })?.response?.data;
  return body?.message || fallback;
}

/** ISO 时间（如 2026-10-30T15:45:18+08:00）→ 常用格式「2026-10-30 15:45」；解析失败原样返回 */
function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function DebatesPage() {
  const navigate = useNavigate();
  const { message, modal } = AntApp.useApp();
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["debates"],
    // 归档页要一屏看到全部历史（含进行中），页大小给足，避免分页把老场次藏起来
    queryFn: () => getDebates({ page_size: 100 }),
    refetchInterval: 3000, // 进行中的比赛状态会变化，轻量轮询
  });
  const items = data?.items ?? [];
  const [filter, setFilter] = useState<"all" | "running" | "finished">("all");
  const filtered = filter === "all"
    ? items
    : filter === "running"
      ? items.filter((d) => d.status === "running" || d.status === "pending") // 进行中含等待启动
      : items.filter((d) => d.status === filter);
  /** 可清理的归档：已结束 / 异常 / 已停止（进行中的必须先在直播页终止） */
  const archived = items.filter((d) => ["finished", "failed", "stopped"].includes(d.status));

  const remove = async (id: number) => {
    try {
      await deleteDebate(id);
      message.success("已删除该场归档");
      await queryClient.invalidateQueries({ queryKey: ["debates"] });
    } catch (err) {
      message.error(errorText(err, "删除失败，请稍后重试"));
    }
  };

  const clearAll = () => {
    modal.confirm({
      title: "清空历史归档？",
      content: `将删除 ${archived.length} 场已结束 / 异常 / 已停止的比赛，连同全部发言与评分卡，删除后不可恢复。进行中的比赛不受影响。`,
      okText: "确认清空",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        try {
          const res = await clearArchivedDebates();
          message.success(`已清空 ${res.deleted} 场归档`);
          await queryClient.invalidateQueries({ queryKey: ["debates"] });
        } catch (err) {
          message.error(errorText(err, "清空失败，请稍后重试"));
        }
      },
    });
  };

  return (
    <div>
      <div className="ws-toolbar">
        <div className="seg">
          {([["all", "全部"], ["running", "进行中"], ["finished", "已结束"]] as const).map(([key, label]) => (
            <button key={key} className={filter === key ? "on" : ""} onClick={() => setFilter(key)}>{label}</button>
          ))}
        </div>
        <div className="ws-actions">
          <button
            className="neu-btn"
            style={{ padding: "7px 16px", color: archived.length ? "var(--con-deep)" : "var(--ink-3)" }}
            disabled={archived.length === 0}
            onClick={clearAll}
          >
            清空归档{archived.length ? `（${archived.length}）` : ""}
          </button>
          <button className="neu-btn" style={{ padding: "7px 16px" }} onClick={() => { window.location.href = "/topics"; }}>返回辩题库</button>
        </div>
      </div>
      <Table
        rowKey="id"
        dataSource={filtered}
        pagination={{ pageSize: 10 }}
        onRow={(record) => ({ onClick: () => navigate(`/debates/${record.id}`), style: { cursor: "pointer" } })}
        columns={[
          { title: "ID", dataIndex: "id", width: 60 },
          { title: "辩题", dataIndex: "topic_title" },
          { title: "赛制", dataIndex: "format", width: 80, render: (v: string) => (v === "full" ? "完整" : "精简") },
          {
            title: "状态", dataIndex: "status", width: 100,
            render: (v: DebateStatus) => (
              <Tag color={STATUS_COLOR[v]}>
                {v === "running" && <span className="status-dot" />}
                {STATUS_CN[v]}
              </Tag>
            ),
          },
          {
            title: "胜方", dataIndex: "winner", width: 80,
            render: (v: string | null) => (v ? (v === "pro" ? <Tag color="blue">正方</Tag> : <Tag color="red">反方</Tag>) : "—"),
          },
          { title: "创建时间", dataIndex: "created_at", width: 160, render: (v: string) => fmtTime(v) },
          {
            title: "操作", width: 90, align: "center" as const,
            render: (_: unknown, record: { id: number; status: string }) => {
              const active = record.status === "running" || record.status === "pending";
              return (
                <Popconfirm
                  title="删除这场归档？"
                  description="发言与评分卡会一并删除，不可恢复。"
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  disabled={active}
                  onConfirm={() => remove(record.id)}
                >
                  <button
                    className="neu-btn"
                    style={{ padding: "4px 12px", fontSize: 12, color: active ? "var(--ink-3)" : "var(--con-deep)" }}
                    disabled={active}
                    title={active ? "进行中的比赛请先在直播页终止" : "删除该场归档"}
                    onClick={(e) => e.stopPropagation()}
                  >
                    删除
                  </button>
                </Popconfirm>
              );
            },
          },
        ]}
      />
    </div>
  );
}
