# ISS-008（P2）`CORS_ORIGINS` 为死配置：`.env` 提供该变量，但后端从未挂载 CORSMiddleware

**状态**：待修复（二选一：接线或删除）
**位置**：`backend/.env`（`CORS_ORIGINS=http://...`）、`backend/src/debate/api/app.py`（无任何 CORS 中间件）

## 结论

`backend/.env` 与 `.env.example` 都定义了 `CORS_ORIGINS`，暗示"支持跨域部署"；但全代码库检索 `CORSMiddleware` / `allow_origins` / `CORS_ORIGINS` 均无消费点（`api/app.py` 未注册任何 CORS 中间件）。当前唯一能正常工作的前端接入方式是 vite dev proxy（`vite.config.ts:12` 把 `/api` 代理到 8000）——即**只能同源部署**。

## 复现

```powershell
Select-String -Path backend\src\debate\**\*.py -Pattern 'CORS'   # 无输出
Get-Content backend\.env | Select-String CORS_ORIGINS             # CORS_ORIGINS=http://localhost:5173
```

直连验证（绕过 vite proxy）：从 `http://localhost:5173` 以外的 origin 直接 fetch `http://127.0.0.1:8000/api/auth/me`，浏览器因缺 `Access-Control-Allow-Origin` 拦截。

## 影响

- 配置与实现不一致：运维按 `.env.example` 配置 CORS 期望跨域可用，上线后前端直连全部被浏览器拦截，排障成本高。
- 若未来有人"顺手"全局放开 `CORSMiddleware(allow_origins=["*"])` 接线，配合 httpOnly Cookie 会引入跨站携带风险——需要在接线时一并明确 `allow_credentials` 策略。

## 修复建议

二选一：

1. **接线（若产品需要跨域）**：`create_app` 里按 `CORS_ORIGINS`（逗号分隔）注册 `CORSMiddleware`，`allow_credentials=True` 仅对显式白名单 origin 开启；补一条跨域单测。
2. **删除（若坚持同源/proxy 部署，推荐）**：从 `.env.example` 移除该键，README/部署文档注明"前端经反向代理同源接入"。

## 验收标准

- 配置与行为一致：要么跨域单测证明白名单生效/非白名单被拒；要么 `.env.example` 不再出现 `CORS_ORIGINS`。
