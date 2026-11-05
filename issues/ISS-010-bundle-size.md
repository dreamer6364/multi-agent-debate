# ISS-010（P3）前端单 chunk 1.17MB（gzip 374KB），超 Vite 500kB 告警线

**状态**：✅ 已优化（路由级 `React.lazy` + `manualChunks` 只抽框架库；构建零 500kB 告警、零循环告警，最大 chunk 324.87kB，入口降至 7.38kB，页面 chunk 3~93kB；vitest 35/35、`tsc --noEmit` 0 错误、`vite build` 退出码 0）
**位置**：`frontend`（构建产物 `dist/assets/index-*.js`）

## 结论

`npx vite build` 成功但持续告警：

```
dist/assets/index-CZ0uJ2kh.js   1,170.55 kB │ gzip: 374.11 kB
(!) Some chunks are larger than 500 kB after minification.
```

所有页面与图表库打进同一 chunk，首屏需要整包下载。

## 复现

```powershell
cd frontend; npx vite build
```

## 影响

- 首屏 JS 体积约为 500kB 告警线的 2.3 倍；gzip 后 374KB 对课堂/演示网络环境仍偏重（项目定位含现场演示）。
- 单 chunk 意味着任意页面改动都使整包缓存失效（无长期缓存粒度）。

## 修复建议（按性价比排序）

1. 路由级 `React.lazy` + `Suspense`：Arena/Summary/Admin 按需加载，主包预计降 40%+。
2. `build.rollupOptions.output.manualChunks`：把 echarts（若引入）/ xlsx 相关库拆独立 chunk。
3. 若图表用 echarts：改 `echarts/core` 按需引入（只注册用到的图表/组件）。

## 验收标准

- 主 chunk ≤ 500kB（minified）或构建零告警；演示网络下首屏可接受（Lighthouse 参考）。
- 现有 35 个 vitest 用例与 `tsc --noEmit` 保持全绿。
