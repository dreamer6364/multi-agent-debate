import { useQuery, useQueryClient } from "@tanstack/react-query";
import { App as AntApp, Button, Card, Form, Input, Modal, Popconfirm, Select, Tag } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { createDebate, createTopic, deleteTopic, listTopics } from "../api/client";
import type { TopicItem } from "../api/types";

function TopicCard({ topic, onLaunch, onDelete }: {
  topic: TopicItem;
  onLaunch: (t: TopicItem) => void;
  onDelete: (id: number) => void;
}) {
  return (
    <Card
      title={topic.title}
      extra={
        <>
          {topic.is_builtin && <Tag color="gold">内置</Tag>}
          {!topic.is_builtin && (
            <Popconfirm title="确认删除该辩题？" onConfirm={() => onDelete(topic.id)}>
              <Button size="small" danger>删除</Button>
            </Popconfirm>
          )}
        </>
      }
      className="topic-card"
      style={{ width: 360 }}
    >
      <p><Tag color="blue">正方</Tag>{topic.pro_stance}</p>
      <p><Tag color="red">反方</Tag>{topic.con_stance}</p>
      <Button type="primary" size="small" className="gradient-btn" onClick={() => onLaunch(topic)}>发起比赛</Button>
    </Card>
  );
}

export default function TopicsPage() {
  const navigate = useNavigate();
  const { message } = AntApp.useApp();
  const queryClient = useQueryClient();
  const { data: topics = [] } = useQuery({ queryKey: ["topics"], queryFn: listTopics });
  const [launchTarget, setLaunchTarget] = useState<TopicItem | null>(null);
  const [customOpen, setCustomOpen] = useState(false);
  const [launchForm] = Form.useForm();
  const [topicForm] = Form.useForm();

  const launch = async (values: { format: string; judges: number }) => {
    if (!launchTarget) return;
    try {
      const created = await createDebate({
        topic_id: launchTarget.id,
        format: values.format as "full" | "lite",
        judges: values.judges,
      });
      message.success("比赛已发起");
      navigate(`/debates/${created.id}`);
    } catch {
      message.error("发起失败（可能已达配额）");
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
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: "flex", justifyContent: "space-between" }}>
        <h2>辩题库</h2>
        <div>
          <Button style={{ marginRight: 8 }} onClick={() => setCustomOpen(true)}>新建辩题</Button>
          <Button href="/debates">历史归档</Button>
        </div>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
        {topics.map((t) => (
          <TopicCard key={t.id} topic={t} onLaunch={setLaunchTarget}
                     onDelete={(id) => void remove(id)} />
        ))}
      </div>

      <Modal
        open={launchTarget !== null}
        title={`发起比赛：${launchTarget?.title ?? ""}`}
        onCancel={() => setLaunchTarget(null)}
        onOk={() => launchForm.submit()}
        okText="开始"
        okButtonProps={{ className: "gradient-btn" }}
      >
        <Form form={launchForm} layout="vertical" onFinish={launch} initialValues={{ format: "full", judges: 3 }}>
          <Form.Item name="format" label="赛制" rules={[{ required: true }]}>
            <Select options={[{ value: "full", label: "完整赛制（13 环节）" }, { value: "lite", label: "精简赛制（5 环节）" }]} />
          </Form.Item>
          <Form.Item name="judges" label="评委人数" rules={[{ required: true }]}>
            <Select options={[{ value: 1, label: "1 位" }, { value: 3, label: "3 位" }, { value: 5, label: "5 位" }]} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={customOpen}
        title="新建辩题"
        onCancel={() => setCustomOpen(false)}
        onOk={() => topicForm.submit()}
        okText="创建"
      >
        <Form form={topicForm} layout="vertical" onFinish={createCustom}>
          <Form.Item name="title" label="辩题（正方命题 / 反方命题）" rules={[{ required: true }]}>
            <Input placeholder="例：人工智能时代，人类应该更依赖技术 / 更保持警惕" />
          </Form.Item>
          <Form.Item name="pro_points" label="正方破题要点" rules={[{ required: true }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="con_points" label="反方破题要点" rules={[{ required: true }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
