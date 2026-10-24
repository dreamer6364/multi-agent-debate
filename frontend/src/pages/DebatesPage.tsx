import { useQuery } from "@tanstack/react-query";
import { Table, Tag } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { getDebates } from "../api/client";
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

export default function DebatesPage() {
  const navigate = useNavigate();
  const { data } = useQuery({
    queryKey: ["debates"],
    queryFn: () => getDebates(),
    refetchInterval: 3000, // 进行中的比赛状态会变化，轻量轮询
  });
  const items = data?.items ?? [];
  const [filter, setFilter] = useState<"all" | "running" | "finished">("all");
  const filtered = filter === "all" ? items : items.filter((d) => d.status === filter);
  return (
    <div>
      <div className="ws-toolbar">
        <div className="seg">
          {([["all", "全部"], ["running", "进行中"], ["finished", "已结束"]] as const).map(([key, label]) => (
            <button key={key} className={filter === key ? "on" : ""} onClick={() => setFilter(key)}>{label}</button>
          ))}
        </div>
        <button className="neu-btn" style={{ padding: "7px 16px" }} onClick={() => { window.location.href = "/topics"; }}>返回辩题库</button>
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
          { title: "创建时间", dataIndex: "created_at", width: 200 },
        ]}
      />
    </div>
  );
}
