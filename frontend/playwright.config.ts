import { readFileSync } from "node:fs";
import { sep } from "node:path";
import { defineConfig } from "@playwright/test";

// E2E-WEB-01（TEST_PLAN §8）：真实 uvicorn（FakeLLM 开发档）+ Vite dev，全栈浏览器测试。
// reuseExistingServer：本地已有 8000/5173 服务时直接复用。

// ISS-005：MySQL 口令不入库——E2E 库连接串从 backend/.env 的 MYSQL_URL 派生（换库名 mad_e2e），
// 或用 TEST_MYSQL_URL 环境变量显式指定；都拿不到时返回空串，dev_uvicorn 回落 .env 默认库。
function e2eDbUrl(): string {
  const explicit = process.env.TEST_MYSQL_URL;
  if (explicit) return explicit;
  let base: string | undefined;
  try {
    const envText = readFileSync(`${process.cwd()}/../backend/.env`, "utf8");
    base = envText.match(/^MYSQL_URL=(.*)$/m)?.[1]?.trim();
  } catch {
    base = undefined;
  }
  if (!base) return "";
  try {
    const url = new URL(base);
    url.pathname = "/mad_e2e";
    if (!url.search) url.searchParams.set("charset", "utf8mb4");
    return url.toString();
  } catch {
    return "";
  }
}

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  workers: 1, // 共享测试库（mad_e2e），串行执行
  use: {
    baseURL: "http://localhost:5175",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      // E2E 专用后端：独立端口 + 独立测试库（DEV_DB_URL，见 backend/scripts/dev_uvicorn.py）
      command: [".venv", "Scripts", "python.exe"].join(sep) + " " + ["scripts", "dev_uvicorn.py"].join(sep) + " 8011",
      cwd: "../backend",
      url: "http://127.0.0.1:8011/api/health",
      env: { DEV_DB_URL: e2eDbUrl() },
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
