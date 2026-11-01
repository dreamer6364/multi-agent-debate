# ISS-007（P2）ruff 共 176 处违规未清理，含 `runtime.py` 3 处 F821 `Any` 未导入（mypy 必挂）等真实缺陷信号

**状态**：待修复
**位置**：`backend/src`（129 处）+ `backend/tests`；重点 `src/debate/engine/runtime.py:35,44,45`

## 结论

ruff 是项目 dev 依赖且 `pyproject.toml` 已配置 `[tool.ruff]`（line-length=100），但 `ruff check src tests` 长期 176 红。其中多数是风格类（unused import、格式），但**混着几类真实缺陷信号**，红灯常态化让它们无法被识别：

1. **F821 Undefined name `Any`**（`runtime.py:35,44,45`）：`from typing import Callable` 漏了 `Any`。因文件头有 `from __future__ import annotations`，注解在运行时不求值所以**运行时侥幸不炸**；但 `get_type_hints`/pydantic 反射/mypy 任一路径都会 `NameError`。这是定时炸弹。
2. **F401 未使用导入**：`api/app.py:15 now_cn`、`api/debates.py:16 render_scorecard_md / :20 CAMP_CN`、`engine/nodes/phases.py:12 DebateCancelled`、`render/markdown.py:7 ScoreCard / :7 Speech`、`render/xlsx.py:12-13` 等——其中 `DebateCancelled` 未使用值得复核：若 phases.py 本应捕获取消异常而漏了，就是并发取消路径的真 bug（当前取消在 runner.py 统一处理，暂判为冗余导入）。
3. **F841 赋值未使用**：`nodes/judges.py:226 qc_started`、`nodes/phases.py:310 exc`——`except ... as exc` 吞异常变量，排障时会丢失上下文线索。

## 复现

```powershell
cd backend
.venv/Scripts/python.exe -m ruff check src tests          # Found 176 errors
.venv/Scripts/python.exe -m ruff check src --output-format=concise | Select-String 'F821|F401|F841'
.venv/Scripts/python.exe -m mypy src                      # runtime.py:35 NameError 级错误
```

## 影响

- 类型系统/反射路径存在运行时 NameError 风险（ISS 级别的隐患而非风格问题）。
- lint 全红 = lint 失去报警功能；无 CI（`.github/workflows` 不存在）时进一步恶化。
- 死代码（未用导入/变量）增加阅读成本，违背项目"Agent 可读性"的自我要求。

## 修复建议

1. 立即：`runtime.py` 补 `from typing import Any, Callable`（一行修复）。
2. 分批清理：`ruff check --fix` 处理 104 个安全修复；F841/F401 人工复核后删除（重点复核 `phases.py` 的 `DebateCancelled` 是否应参与取消处理）。
3. 机制：pre-commit 或 CI 加 `ruff check` 门禁，从"全红"切到"零新债"。

## 验收标准

- `ruff check src tests` → 0；`mypy src` 不再因 F821 失败。
- 复核记录：`DebateCancelled`/`qc_started`/`exc` 三处的删除或保留结论写入提交说明。
