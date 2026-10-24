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

export default function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#1d39c4",
          borderRadius: 8,
          fontFamily: '"PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif',
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
