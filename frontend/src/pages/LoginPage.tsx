import { Card, Form, Input, Button, Tabs, App as AntApp } from "antd";
import { useNavigate } from "react-router-dom";
import { login, register } from "../api/client";

function LoginForm({ onDone }: { onDone: () => void }) {
  const { message } = AntApp.useApp();
  const [form] = Form.useForm();
  return (
    <Form
      form={form}
      layout="vertical"
      onFinish={async (values: { username: string; password: string }) => {
        try {
          await login(values.username, values.password);
          message.success("登录成功");
          onDone();
        } catch {
          message.error("用户名或密码错误");
        }
      }}
    >
      <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
        <Input autoComplete="username" />
      </Form.Item>
      <Form.Item name="password" label="密码" rules={[{ required: true }]}>
        <Input.Password autoComplete="current-password" />
      </Form.Item>
      <Button type="primary" htmlType="submit" block>
        登录
      </Button>
    </Form>
  );
}

function RegisterForm({ onDone }: { onDone: () => void }) {
  const { message } = AntApp.useApp();
  return (
    <Form
      layout="vertical"
      onFinish={async (values: { username: string; password: string }) => {
        try {
          await register(values.username, values.password);
          await login(values.username, values.password);
          message.success("注册并登录成功");
          onDone();
        } catch {
          message.error("注册失败（用户名可能已被占用）");
        }
      }}
    >
      <Form.Item name="username" label="用户名" rules={[
        { required: true },
        { pattern: /^[a-zA-Z0-9_]{3,32}$/, message: "3~32 位字母数字下划线" },
      ]}>
        <Input autoComplete="username" />
      </Form.Item>
      <Form.Item name="password" label="密码" rules={[
        { required: true },
        { min: 8, message: "密码至少 8 位" },
      ]}>
        <Input.Password autoComplete="new-password" />
      </Form.Item>
      <Button type="primary" htmlType="submit" block>
        注册并登录
      </Button>
    </Form>
  );
}

export default function LoginPage() {
  const navigate = useNavigate();
  return (
    <div style={{ display: "flex", justifyContent: "center", paddingTop: 80 }}>
      <Card title="多 Agent 对抗辩论模拟" style={{ width: 380 }}>
        <Tabs
          items={[
            { key: "login", label: "登录", children: <LoginForm onDone={() => navigate("/topics")} /> },
            { key: "register", label: "注册", children: <RegisterForm onDone={() => navigate("/topics")} /> },
          ]}
        />
      </Card>
    </div>
  );
}
