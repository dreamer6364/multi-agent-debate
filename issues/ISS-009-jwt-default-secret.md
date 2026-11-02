# ISS-009（P2）`JWT_SECRET` 缺省回落到内置 `dev-insecure-secret`：忘配环境变量即可伪造任意用户会话

**状态**：✅ 已修复（生产档 `DEBATE_ENV=prod` 缺密钥拒绝启动；开发档改每次启动随机密钥 + 告警日志，固定公开值移除。回归测试 `test_jwt_secret_prod_requires_explicit_value` / `test_jwt_secret_dev_random_per_start`）
**位置**：`backend/src/debate/api/app.py:87`

## 结论

```python
secret = jwt_secret or os.environ.get("JWT_SECRET", "dev-insecure-secret")
```

`JWT_SECRET` 未设置时，服务静默使用**写在源码里的公开默认值**签发会话。攻击者知道默认值即可离线签出任意 `sub=<他人 user id>` 的 token，直接接管任何账号。这是经典的"默认密钥"漏洞类（CWE-798 / A02 加密失败）。

叠加因素：

- `.dockerignore` 排除 `.env`，而 `load_env` 对缺失文件是静默跳过（`core/config.py:199` `if file.exists()`）——容器部署**天然容易**落进"无 JWT_SECRET"状态（与 ISS-004 的镜像问题组合时必然发生）。
- `COOKIE_SECURE` 默认 False，明文 HTTP 下 Cookie 可被窃听（开发可接受，生产需显式开启，属同一硬化主题）。

## 复现

```powershell
# 不设 JWT_SECRET 启动服务，然后用已知默认值签 token：
# secret = "dev-insecure-secret" → create_access_token(sub=其他用户id)
# 携带该 Cookie 访问 /api/auth/me → 200，身份为他人
```

反向验证（当前测试环境）：测试注入 `test-secret` 时，错 secret 的 token 正确返回 401（`evidence/_diag_race_auth.py`），机制本身没问题——问题只在默认值这条路。

## 影响

- 生产忘配一个环境变量 = 全站账号可被任意接管，且无任何启动告警。
- 与"安全默认值"原则相悖：不安全配置必须显式选择，而不是默认存在。

## 修复建议

1. 生产模式强制无默认值：`create_app(..., env="production")` 或检测到非 dev 时，`JWT_SECRET` 缺失直接 `raise RuntimeError("生产启动必须设置 JWT_SECRET")`（fail-fast，与 load_corpus/roles 同策略）。
2. dev 需要便利时，改为**每次启动随机生成**并打日志提示"会话将在重启后失效"，而不是使用固定公开值。
3. 文档与 `.env.example` 同步标注必填；容器启动脚本做存在性检查。

## 验收标准

- 不设 `JWT_SECRET` 时进程**拒绝启动**（或随机密钥 + 明确告警），二选一落地。
- 回归：启动检查的单测（缺失 → 抛错；设置 → 正常签发/校验）。
