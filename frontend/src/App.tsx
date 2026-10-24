import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./components/AppLayout";
import ArenaPage from "./pages/ArenaPage";
import DebatesPage from "./pages/DebatesPage";
import LoginPage from "./pages/LoginPage";
import TopicsPage from "./pages/TopicsPage";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

// Neumorphism 主题：单色系 #e0e5ec，AntD 全面去白底/去边框，阴影走双光源
const NEU_DARK = "#b8bcc2";
const NEU_LIGHT = "#ffffff";

export default function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#6d5dfc",
          colorInfo: "#6d5dfc",
          colorBgLayout: "#e0e5ec",
          colorBgContainer: "#e0e5ec",
          colorBgElevated: "#f0f0f3",
          colorBgMask: "rgba(120, 130, 150, 0.45)",
          colorBorder: "#e0e5ec",
          colorBorderSecondary: "#d6dbe6",
          colorText: "#333333",
          colorTextSecondary: "#566070",
          colorTextTertiary: "#9aa2b1",
          borderRadius: 12,
          controlHeight: 38,
          fontFamily: '"PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif',
          boxShadow: `8px 8px 16px ${NEU_DARK}, -8px -8px 16px ${NEU_LIGHT}`,
          boxShadowSecondary: `6px 6px 12px ${NEU_DARK}, -6px -6px 12px ${NEU_LIGHT}`,
        },
        components: {
          Button: {
            primaryShadow: `6px 6px 12px ${NEU_DARK}, -6px -6px 12px ${NEU_LIGHT}`,
            defaultShadow: `6px 6px 12px ${NEU_DARK}, -6px -6px 12px ${NEU_LIGHT}`,
            dangerShadow: `6px 6px 12px ${NEU_DARK}, -6px -6px 12px ${NEU_LIGHT}`,
          },
          Input: {
            activeBorderColor: "#6d5dfc",
            hoverBorderColor: "#b8bcc2",
            activeShadow: `inset 2px 2px 4px ${NEU_DARK}, inset -2px -2px 4px ${NEU_LIGHT}`,
          },
          Select: { optionSelectedBg: "#e0e5ec", optionActiveBg: "#f0f0f3" },
          Table: { headerBg: "#e0e5ec", headerSplitColor: "transparent", rowHoverBg: "#f0f0f3", borderColor: "#d6dbe6" },
          Modal: { contentBg: "#f0f0f3", headerBg: "#f0f0f3" },
          Pagination: { itemActiveBg: "#e0e5ec" },
          Tabs: { inkBarColor: "#6d5dfc", itemSelectedColor: "#333333", itemColor: "#566070" },
        },
      }}
    >
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <Routes>
            <Route path="/" element={<Navigate to="/topics" replace />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/topics" element={<AppLayout><TopicsPage /></AppLayout>} />
            <Route path="/debates" element={<AppLayout><DebatesPage /></AppLayout>} />
            <Route path="/debates/:id" element={<AppLayout><ArenaPage /></AppLayout>} />
            <Route path="*" element={<Navigate to="/topics" replace />} />
          </Routes>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  );
}
