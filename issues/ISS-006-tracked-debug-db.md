# ISS-006（P2）调试残留 `backend/._sse_dbg/test.db`（SQLite 61KB）被 git 追踪

**状态**：待修复
**位置**：`backend/._sse_dbg/test.db`

## 结论

v1.0 首个提交（`0a69c56`）误将调试目录 `backend/._sse_dbg/` 连同其 SQLite 数据库 `test.db`（61,440 字节）提交入库，至今仍在版本库。`.gitignore` 只覆盖 `debate-*.db`，未覆盖该路径（`git check-ignore` 不命中）。

## 复现

```powershell
git ls-files backend/._sse_dbg     # → backend/._sse_dbg/test.db
git log --oneline -- backend/._sse_dbg/test.db   # → 0a69c56 feat: 多 Agent 对抗辩论模拟平台 v1.0 全栈版本
(Get-Item backend/._sse_dbg/test.db).Length      # → 61440
```

## 影响

- 仓库卫生：二进制测试残留永久留在历史里，克隆即得一份来路不明的数据库文件。
- 潜在信息面：文件名前缀 `._` 是 macOS 资源叉残留的惯用形态，说明是误提交；其中若含历史调试数据（用户/场次）即为数据残留。
- 与根目录 `.gitignore` 的 `debate-*.db` 规则精神不一致，属于清理遗漏。

## 修复建议

```powershell
git rm --cached backend/._sse_dbg/test.db
Remove-Item -Recurse backend/._sse_dbg          # 本地一并删除
# .gitignore 追加：
#   *_dbg/
#   *.db（或至少 backend/**/*.db）
git commit -m "chore: 移除误入库的调试残留 ._sse_dbg/test.db"
```

历史重写（filter-repo）非必需——该文件不含密钥，按团队策略决定是否清理历史。

## 验收标准

- `git ls-files | Select-String '\.db$'` 无输出。
- `.gitignore` 覆盖 `*.db` 与调试目录，`git status` 干净。
