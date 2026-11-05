/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiTarget = process.env.DEV_API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": apiTarget }, // dev 代理到 FastAPI（CONSTRAINTS §1.2）；E2E 经 DEV_API_TARGET 指向独立后端
  },
  build: {
    // ISS-010：只把框架库（react 家族/路由/数据层）拆成独立 chunk——入口只留业务代码；
    // antd/rc-* 不做整包强拆（会造出 >500kB 大块并引入 chunk 循环），随默认图按使用方分布。
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          if (/[\\/]node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler|@tanstack)[\\/]/.test(id)) {
            return "vendor";
          }
          return undefined;
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    // Playwright E2E 用例（e2e/*.spec.ts）由 playwright.config.ts 单独运行，不进 vitest
    exclude: ["**/node_modules/**", "e2e/**"],
  },
});
