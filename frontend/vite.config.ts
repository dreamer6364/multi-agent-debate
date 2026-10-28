/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiTarget = process.env.DEV_API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": apiTarget }, // dev 代理到 FastAPI（CONSTRAINTS §1.2）；E2E 经 DEV_API_TARGET 指向独立后端
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    // Playwright E2E 用例（e2e/*.spec.ts）由 playwright.config.ts 单独运行，不进 vitest
    exclude: ["**/node_modules/**", "e2e/**"],
  },
});
