import { useQuery, useQueryClient } from "@tanstack/react-query";
import { App as AntApp, Form, Input, Modal, Select } from "antd";
import { useState } from "react";
import { createDebate, createTopic, deleteTopic, getDebates, listTopics } from "../api/client";
import type { TopicItem } from "../api/types";

/** 立场摘要：取破题要点前 2 条（顿号连接），多余的以"N 点"收尾——真正的观点，而非重复命题。 */
function stanceSummary(raw: string): string {
  const points = raw.split("\n").map((line) => line.trim()).filter(Boolean);
  if (points.length === 0) return "—";
  const head = points.slice(0, 2).join(" · ");
  return points.length > 2 ? `${head} 等 ${points.length} 点` : head;
}

function TopicRow({ topic, onLaunch, onDelete }: {
  topic: TopicItem;
  onLaunch: (t: TopicItem) => void;
  onDelete: (t: TopicItem) => void;
}) {
  return (
    <div className="topic-row">
      <span className="topic-glyph">辩</span>
      <div className="topic-row-main">
        <div className="topic-row-title">
          {topic.title}
          {topic.is_builtin && <span className="src-badge">内置</span>}
          {!topic.is_builtin && (
            <button
              className="neu-btn"
              style={{ padding: "2px 10px", fontSize: 12, marginLeft: 10, color: "var(--con-deep)" }}
              title="从辩题库删除"
              onClick={(e) => { e.stopPropagation(); onDelete(topic); }}
            >
              删除
            </button>
          )}
        </div>
        <div className="topic-row-stance">
          <b className="pro">正方观点</b> {stanceSummary(topic.pro_points)}
          <span style={{ margin: "0 8px", color: "var(--ink-3)" }}>|</span>
          <b className="con">反方观点</b> {stanceSummary(topic.con_points)}
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
  const { message, modal } = AntApp.useApp();
  const queryClient = useQueryClient();
  const { data: topics = [] } = useQuery({ queryKey: ["topics"], queryFn: listTopics });
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

  /** 后端错误信封的 message / code（errors.py），用于区分"被引用"与其它失败 */
  const errInfo = (err: unknown) =>
    (err as { response?: { data?: { code?: string; message?: string } } })?.response?.data ?? {};

  /** 辩题被历史场次引用时的二次确认：连带删除这些场次（发言/评分卡一并清掉） */
  const forceRemove = (topic: TopicItem) => {
    modal.confirm({
      title: "连同历史场次一起删除？",
      content: `「${topic.title}」名下已有比赛记录。删除辩题会同时删除这些场次的全部发言与评分卡，不可恢复；若有进行中的场次，请先终止比赛。`,
      okText: "一起删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await deleteTopic(topic.id, true);
          message.success("辩题与相关历史已删除");
          await queryClient.invalidateQueries({ queryKey: ["topics"] });
          await queryClient.invalidateQueries({ queryKey: ["debates"] });
        } catch (err) {
          message.error(errInfo(err).message || "删除失败");
        }
      },
    });
  };

  const remove = (topic: TopicItem) => {
    modal.confirm({
      title: "删除辩题？",
      content: `「${topic.title}」将从辩题库移除。`,
      okText: "删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await deleteTopic(topic.id);
          message.success("辩题已删除");
          await queryClient.invalidateQueries({ queryKey: ["topics"] });
        } catch (err) {
          const info = errInfo(err);
          if (info.code === "TOPIC_IN_USE") {
            // 先关掉当前弹窗，再问是否连带删除历史（避免两个确认框叠在一起）
            window.setTimeout(() => forceRemove(topic), 120);
            return;
          }
          message.error(info.message || "删除失败");
        }
      },
    });
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
          <div className="stat-label">发起场次</div>
          <div className="stat-value stat-accent">不限</div>
          <div className="stat-hint">随时开赛 · 并发限 1 场</div>
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
          <TopicRow key={t.id} topic={t} onLaunch={setLaunchTarget} onDelete={remove} />
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
