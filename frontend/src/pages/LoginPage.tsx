import { Form, Input, Tabs, App as AntApp } from "antd";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { login, register } from "../api/client";

/** 错误分类（v1.4.3）：网络/服务不可达 ≠ 凭证错误，禁止笼统提示误导用户。 */
function authErrorMessage(err: unknown, fallback: string): string {
  const e = err as { response?: { status?: number } };
  if (!e || e.response === undefined) {
    return "无法连接服务器：请确认后端已启动（双击 start.bat），稍后重试";
  }
  return fallback;
}

const SELLING_POINTS = [
  { icon: "播", title: "逐字直播", desc: "Agent 发言打字机实时渲染" },
  { icon: "评", title: "独立裁决", desc: "三位评委引用原话打分" },
  { icon: "档", title: "全程归档", desc: "实录与 HTML 报告一键导出" },
];

function LoginForm({ onDone }: { onDone: () => void }) {
  const { message } = AntApp.useApp();
  const [form] = Form.useForm();
  return (
    <Form
      form={form}
      layout="vertical"
      requiredMark={false}
      onFinish={async (values: { username: string; password: string }) => {
        try {
          await login(values.username, values.password);
          message.success("登录成功");
          onDone();
        } catch (err) {
          message.error(authErrorMessage(err, "用户名或密码错误"));
        }
      }}
    >
      <Form.Item name="username" label="用户名" rules={[{ required: true, message: "请输入用户名" }]}>
        <Input autoComplete="username" placeholder="你的用户名" />
      </Form.Item>
      <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
        <Input.Password autoComplete="current-password" placeholder="••••••••" />
      </Form.Item>
      <button className="neu-btn-accent" style={{ width: "100%", padding: "11px 0", fontSize: 15, fontWeight: 600 }}>
        登 录
      </button>
    </Form>
  );
}

function RegisterForm({ onDone }: { onDone: () => void }) {
  const { message } = AntApp.useApp();
  return (
    <Form
      layout="vertical"
      requiredMark={false}
      onFinish={async (values: { username: string; password: string }) => {
        try {
          await register(values.username, values.password);
          await login(values.username, values.password);
          message.success("注册并登录成功");
          onDone();
        } catch (err) {
          const e = err as { response?: { status?: number } };
          if (e?.response?.status === 409) {
            message.error("用户名已被占用，请换一个");
          } else {
            message.error(authErrorMessage(err, "注册失败，请稍后重试"));
          }
        }
      }}
    >
      <Form.Item name="username" label="用户名" rules={[
        { required: true, message: "请输入用户名" },
        { pattern: /^[a-zA-Z0-9_]{3,32}$/, message: "3~32 位字母数字下划线" },
      ]}>
        <Input autoComplete="username" placeholder="3~32 位字母数字下划线" />
      </Form.Item>
      <Form.Item name="password" label="密码" rules={[
        { required: true, message: "请输入密码" },
        { min: 8, message: "密码至少 8 位" },
      ]}>
        <Input.Password autoComplete="new-password" placeholder="至少 8 位" />
      </Form.Item>
      <button className="neu-btn-accent" style={{ width: "100%", padding: "11px 0", fontSize: 15, fontWeight: 600 }}>
        创建我的席位
      </button>
    </Form>
  );
}

export default function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const next = searchParams.get("next") ?? "/topics";
  const [activeTab, setActiveTab] = useState("login");

  return (
    <div className="login-page">
      {/* 背景装饰：柔和的大圆（同色系，低对比） */}
      <div className="login-deco login-deco-raised" aria-hidden="true" />
      <div className="login-deco login-deco-inset" aria-hidden="true" />

      <div className="login-panel neu-raised">
        {/* 左侧：品牌叙事（移动端堆叠到表单上方） */}
        <div className="login-brand-side">
          <div className="brand" style={{ marginBottom: 18 }}>
            <span className="brand-mark">辩</span>
            多 Agent 对抗辩论模拟
          </div>
          <h1 className="login-hero-title">当 AI 开始辩论</h1>
          <p className="login-hero-sub">
            两队大模型 Agent 各派四名辩手，主席控场、计时员守预算、三位评委独立裁决——
            按真实赛制从立论打到总结陈词。
          </p>
          <div className="login-points">
            {SELLING_POINTS.map((point) => (
              <div key={point.icon} className="login-point">
                <span className="feature-icon" style={{ width: 32, height: 32, fontSize: 13 }}>{point.icon}</span>
                <span>
                  <strong style={{ color: "var(--ink)", fontSize: 13 }}>{point.title}</strong>
                  <span style={{ display: "block", color: "var(--ink-2)", fontSize: 12 }}>{point.desc}</span>
                </span>
              </div>
            ))}
          </div>
          <div className="login-duel" aria-hidden="true">
            <span className="duel-chip duel-pro" style={{ padding: "6px 18px", fontSize: 13 }}>正方</span>
            <span className="duel-vs" style={{ width: 40, height: 40, fontSize: 13 }}>辩</span>
            <span className="duel-chip duel-con" style={{ padding: "6px 18px", fontSize: 13 }}>反方</span>
          </div>
        </div>

        {/* 右侧：表单 */}
        <div className="login-form-side">
          <div style={{ marginBottom: 12, minHeight: 44 }}>
            {activeTab === "login" ? (
              <>
                <div style={{ color: "var(--ink)", fontWeight: 600, fontSize: 15 }}>欢迎回来</div>
                <div style={{ color: "var(--ink-2)", fontSize: 13, marginTop: 2 }}>
                  你的辩场还在原地——输入用户名继续观战或开赛。
                </div>
              </>
            ) : (
              <>
                <div style={{ color: "var(--ink)", fontWeight: 600, fontSize: 15 }}>创建你的席位</div>
                <div style={{ color: "var(--ink-2)", fontSize: 13, marginTop: 2 }}>
                  三步开赛：选辩题、定赛制、看直播。每用户每日 5 场配额。
                </div>
              </>
            )}
          </div>
          <Tabs
            activeKey={activeTab}
            onChange={setActiveTab}
            items={[
              { key: "login", label: "登录", children: <LoginForm onDone={() => navigate(next)} /> },
              { key: "register", label: "注册", children: <RegisterForm onDone={() => navigate(next)} /> },
            ]}
          />
        </div>
      </div>
    </div>
  );
}
