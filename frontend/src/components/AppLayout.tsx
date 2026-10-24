import { ConfigProvider } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, useNavigate } from "react-router-dom";
import { logout, me } from "../api/client";

const NAV_ITEMS = [
  { to: "/", label: "开始", end: true },
  { to: "/topics", label: "辩题库" },
  { to: "/debates", label: "历史归档" },
];

/** 登录后工作台壳：左侧栏导航 + 右主区（与开始页的居中海报式区分，同一新拟物家族）。 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: me, retry: false });

  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: "#6d5dfc",
          colorInfo: "#6d5dfc",
          colorBgLayout: "#e0e5ec",
          colorBgContainer: "#e0e5ec",
          colorBgElevated: "#f0f0f3",
          colorBorder: "#e0e5ec",
          colorBorderSecondary: "#d6dbe6",
          colorText: "#333333",
          colorTextSecondary: "#566070",
          colorTextTertiary: "#9aa2b1",
          borderRadius: 12,
          fontFamily: '"PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif',
        },
      }}
    >
      <div className="ws-shell">
        <aside className="ws-sidebar">
          <div className="ws-brand">
            <span className="brand-mark">辩</span>
            <span>对抗辩论模拟</span>
          </div>
          <nav className="ws-nav">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) => (isActive ? "nav-chip active" : "nav-chip")}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ws-user">
            <div className="ws-user-name">
              <span className="ws-avatar">{(user?.username ?? "?").slice(0, 1).toUpperCase()}</span>
              {user?.username ?? "…"}
            </div>
            <button
              className="neu-btn"
              style={{ padding: "6px 0", fontSize: 13 }}
              onClick={async () => {
                await logout();
                queryClient.clear();
                navigate("/login");
              }}
            >
              退出登录
            </button>
          </div>
        </aside>
        <main className="ws-main">{children}</main>
      </div>
    </ConfigProvider>
  );
}
