import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, useNavigate } from "react-router-dom";
import { logout, me } from "../api/client";

/** 品牌页头（凸起面板 + 凹陷激活态导航）+ 内容容器。 */
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
          <NavLink to="/" end className={({ isActive }) => (isActive ? "nav-chip active" : "nav-chip")}>开始</NavLink>
          <NavLink to="/topics" className={({ isActive }) => (isActive ? "nav-chip active" : "nav-chip")}>辩题库</NavLink>
          <NavLink to="/debates" className={({ isActive }) => (isActive ? "nav-chip active" : "nav-chip")}>历史归档</NavLink>
        </nav>
        <div className="user-box">
          <span>{user?.username ?? "…"}</span>
          <button
            className="neu-btn"
            style={{ padding: "5px 14px", fontSize: 13 }}
            onClick={async () => {
              await logout();
              queryClient.clear();
              navigate("/login");
            }}
          >
            退出
          </button>
        </div>
      </header>
      {children}
    </div>
  );
}
