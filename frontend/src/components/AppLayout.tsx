import { Button } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, useNavigate } from "react-router-dom";
import { logout, me } from "../api/client";

/** 品牌页头 + 内容容器（全站统一导航、当前用户、退出）。 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: me, retry: false });

  return (
    <div style={{ minHeight: "100vh" }}>
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">辩</span>
          多 Agent 对抗辩论模拟
        </div>
        <nav>
          <NavLink to="/topics" className={({ isActive }) => (isActive ? "active" : "")}>辩题库</NavLink>
          <NavLink to="/debates" className={({ isActive }) => (isActive ? "active" : "")}>历史归档</NavLink>
        </nav>
        <div className="user-box">
          <span>{user?.username ?? "…"}</span>
          <Button
            size="small"
            ghost
            onClick={async () => {
              await logout();
              queryClient.clear();
              navigate("/login");
            }}
          >
            退出
          </Button>
        </div>
      </header>
      {children}
    </div>
  );
}
