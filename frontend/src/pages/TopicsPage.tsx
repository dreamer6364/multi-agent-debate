import { useQuery, useQueryClient } from "@tanstack/react-query";
import { App as AntApp, Form, Input, Modal, Select } from "antd";
import { useState } from "react";
import { createDebate, createTopic, deleteTopic, getDebates, listTopics, me } from "../api/client";
import type { TopicItem } from "../api/types";

function TopicRow({ topic, onLaunch, onDelete }: {
  topic: TopicItem;
  onLaunch: (t: TopicItem) => void;
  onDelete: (id: number) => void;
}) {
  return (
    <div className="topic-row">
      <span className="topic-glyph">辩</span>
      <div className="topic-row-main">
        <div className="topic-row-title">
          {topic.title}
          {topic.is_builtin && <span className="src-badge">内置</span>}
          {!topic.is_builtin && (
            <a
              href="#"
              onClick={(e) => { e.preventDefault(); onDelete(topic.id); }}
              style={{ fontSize: 12, color: "var(--con-deep)", marginLeft: 10 }}
            >
              删除
            </a>
          )}
        </div>
        <div className="topic-row-stance">
          <b className="pro">正</b> {topic.pro_stance}　<b className="con">反</b> {topic.con_stance}
        </div>
      </div>
      <button
        className="neu-btn-accent"
        style={{ padding: "9px 20px", fontSize: 14, fontWeight: 600 }}
        onClick={() => onLaunch(topic)}
      >
        开赛
      </button>
    </div>
  );
}

export default function TopicsPage() {
  const { message } = AntApp.useApp();
  const queryClient = useQueryClient();
  const { data: topics = [] } = useQuery({ queryKey: ["topics"], queryFn: listTopics });
  const { data: meInfo } = useQuery({ queryKey: ["me"], queryFn: me, retry: false });
  const { data: debatesData } = useQuery({
    queryKey: ["debates"],
    queryFn: () => getDebates({ page_size: 100 }),
    refetchInterval: 5000,
  });
  const [launchTarget, setLaunchTarget] = useState<TopicItem | null>(null);
  const [customOpen, setCustomOpen] = useState(false);
  const [launchForm] = Form.useForm();
  const [topicForm] = Form.useForm();

  const debates = debatesData?.items ?? [];
  const running = debates.filter((d) => d.status === "running" || d.status === "pending");
  const last = debates.find((d) => d.status === "finished");
  const quota = meInfo?.quota;
  const lastText = last
    ? `${last.winner === "pro" ? "正方胜" : last.winner === "con" ? "反方胜" : "平局"} · ${last.format === "full" ? "完整赛制" : "精简赛制"}`
    : "还没有完成的对局";

  const launch = async (values: { format: string; judges: number }) => {
    if (!launchTarget) return;
    try {
      const created = await createDebate({
        topic_id: launchTarget.id,
        format: values.format as "full" | "lite",
        judges: values.judges,
      });
      message.success("比赛已发起，正在进入直播");
      window.location.href = `/debates/${created.id}`;
    } catch {
      message.error("发起失败（可能已达配额或已有进行中的比赛）");
    }
  };

  const createCustom = async (values: { title: string; pro_points: string; con_points: string }) => {
    try {
      await createTopic(values);
      message.success("辩题已创建");
      setCustomOpen(false);
      topicForm.resetFields();
      await queryClient.invalidateQueries({ queryKey: ["topics"] });
    } catch {
      message.error("创建失败");
    }
  };

  const remove = async (id: number) => {
    try {
      await deleteTopic(id);
      await queryClient.invalidateQueries({ queryKey: ["topics"] });
    } catch {
      message.error("删除失败（可能已被比赛引用）");
    }
  };

  return (
    <div>
      {/* 概览面板：操作语言，零介绍文案 */}
      <div className="stat-row">
        <div className="neu-raised stat-card">
          <div className="stat-label">进行中</div>
          <div className="stat-value stat-accent">{running.length}</div>
          <div className="stat-hint">{running.length > 0 ? "历史归档可回到直播" : "当前没有比赛在跑"}</div>
        </div>
        <div className="neu-raised stat-card">
          <div className="stat-label">已完成对局</div>
          <div className="stat-value">{debates.filter((d) => d.status === "finished").length}</div>
          <div className="stat-hint">全部可回看 · 支持导出</div>
        </div>
        <div className="neu-raised stat-card">
          <div className="stat-label">今日配额</div>
          <div className="stat-value">
            {quota ? quota.daily_limit - quota.daily_used : "–"}
            <span className="stat-unit">/ {quota?.daily_limit ?? "–"} 场剩余</span>
          </div>
          <div className="stat-hint">并发限 1 场 · 每日自然重置</div>
        </div>
        <div className="neu-raised stat-card">
          <div className="stat-label">最近赛果</div>
          <div className="stat-value" style={{ fontSize: 17, paddingTop: 4 }}>{lastText}</div>
          <div className="stat-hint">{last ? last.topic_title.slice(0, 14) + "…" : "选一道题开赛吧"}</div>
        </div>
      </div>

      {/* 工具条 + 紧凑列表 */}
      <div className="ws-toolbar">
        <h2 className="ws-title">选一道辩题开赛
          <span style={{ fontSize: 12, fontWeight: 400, color: "var(--ink-2)", marginLeft: 10 }}>
            {topics.length} 道可选
          </span>
        </h2>
        <div className="ws-actions">
          <button className="neu-btn" style={{ padding: "7px 16px" }} onClick={() => setCustomOpen(true)}>自建辩题</button>
        </div>
      </div>

      <div className="topic-list">
        {topics.map((t) => (
          <TopicRow key={t.id} topic={t} onLaunch={setLaunchTarget} onDelete={(id) => void remove(id)} />
        ))}
      </div>

      <Modal
        open={launchTarget !== null}
        title={`开赛：${launchTarget?.title ?? ""}`}
        onCancel={() => setLaunchTarget(null)}
        onOk={() => launchForm.submit()}
        okText="开始比赛"
        okButtonProps={{ className: "neu-btn-accent", style: { border: "none" } }}
      >
        <Form form={launchForm} layout="vertical" onFinish={launch} initialValues={{ format: "full", judges: 3 }}>
          <Form.Item name="format" label="赛制" rules={[{ required: true }]}>
            <Select options={[{ value: "full", label: "完整赛制（13 环节，约 15-20 分钟）" }, { value: "lite", label: "精简赛制（5 环节，约 5 分钟）" }]} />
          </Form.Item>
          <Form.Item name="judges" label="评委人数" rules={[{ required: true }]}>
            <Select options={[{ value: 1, label: "1 位" }, { value: 3, label: "3 位（推荐）" }, { value: 5, label: "5 位" }]} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={customOpen}
        title="自建辩题"
        onCancel={() => setCustomOpen(false)}
        onOk={() => topicForm.submit()}
        okText="创建"
        okButtonProps={{ className: "neu-btn-accent", style: { border: "none" } }}
      >
        <Form form={topicForm} layout="vertical" onFinish={createCustom}>
          <Form.Item name="title" label="辩题（正方命题 / 反方命题）" rules={[{ required: true }]}>
            <Input placeholder="例：人工智能时代，人类应该更依赖技术 / 更保持警惕" />
          </Form.Item>
          <Form.Item name="pro_points" label="正方破题要点" rules={[{ required: true }]}>
            <Input.TextArea rows={3} placeholder="每行一个要点，仅正方可见" />
          </Form.Item>
          <Form.Item name="con_points" label="反方破题要点" rules={[{ required: true }]}>
            <Input.TextArea rows={3} placeholder="每行一个要点，仅反方可见" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
