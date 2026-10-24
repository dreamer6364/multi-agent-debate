import { sep } from "node:path";
import { defineConfig } from "@playwright/test";

// E2E-WEB-01（TEST_PLAN §8）：真实 uvicorn（FakeLLM 开发档）+ Vite dev，全栈浏览器测试。
// reuseExistingServer：本地已有 8000/5173 服务时直接复用。
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  workers: 1, // 共享 SQLite 开发库，串行执行
  use: {
    baseURL: "http://localhost:5175",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      // E2E 专用后端：独立端口 + 独立 SQLite（DEBATE_DB_URL），不碰本地 8000 的真实服务
      command: [".venv", "Scripts", "python.exe"].join(sep) + " " + ["scripts", "dev_uvicorn.py"].join(sep) + " 8011",
      cwd: "../backend",
      url: "http://127.0.0.1:8011/api/health",
      env: { DEV_DB_URL: "sqlite+aiosqlite:///./debate-e2e.db" },
      timeout: 60_000,
    },
    {
      command: "npm run dev -- --port 5175 --strictPort",
      url: "http://localhost:5175",
      env: { DEV_API_TARGET: "http://127.0.0.1:8011" },
      timeout: 60_000,
    },
  ],
});
