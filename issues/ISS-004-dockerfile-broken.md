# ISS-004（P1）Dockerfile：`COPY README.md*` 指向不存在文件（构建必失败），且缺 `COPY knowledge`（容器内首场比赛必崩）

**状态**：待修复（静态取证，测试机 docker daemon 未启动无法实测构建）
**位置**：`backend/Dockerfile`

## 结论

两个叠加缺陷，任一都足以让容器化部署不可用：

1. **构建阶段**：`COPY pyproject.toml README.md* ./` 中 `backend/README.md` 不存在（`Test-Path backend\README.md` = False）。Docker 对通配符源目录的规则是"匹配到 0 个文件即构建失败"（`no source files were specified`），因此 `docker build` 大概率在第 5 行直接失败。
2. **运行阶段**：即使构建通过（假设 README 问题被绕过），Dockerfile 只 COPY 了 `config.yaml/rules/roles/topics`，**没有 `COPY knowledge ./knowledge`**。而 `config.rag.enabled` 默认 `True`（`core/config.py:96`，config.yaml 未关闭），每场比赛启动都会执行 `EvidenceRetriever.from_dir(root / "knowledge")` → `load_corpus` 目录不存在即 `raise CorpusError`（`rag/corpus.py:65-66`，fail-fast 策略）——**容器里任何一场比赛创建即崩**。

## 复现（静态推演）

```dockerfile
COPY pyproject.toml README.md* ./   # backend/README.md 不存在 → 构建失败
...
COPY config.yaml ./                 # 以下仅 4 项资产
COPY rules ./rules
COPY roles ./roles
COPY topics ./topics                # 缺 knowledge/
```

运行时链路：`api/debates.py create` → 引擎线程 `runner.py:54-56`（`if config.rag.enabled` → `from_dir`）→ `corpus.py:65` `raise CorpusError(f"证据库目录不存在: {directory}")` → 场次 status=failed。

补充：`.dockerignore` 排除了 `.env`，容器内无 `JWT_SECRET` → 还会回落到 `dev-insecure-secret`（见 ISS-009）。

## 影响

- 部署路径（README/Dockerfile 承诺的容器化）完全不可用；CI 若有镜像构建步骤会第一时间红。
- 与 `load_corpus` 的 fail-fast 设计初衷相反：资产缺失本应在**构建期**暴露，现在被推迟到用户创建第一场比赛时才崩。

## 修复建议

```dockerfile
COPY pyproject.toml README.md* ./   # 要么删除 README.md*，要么在 backend/ 放一份 README
COPY src ./src
RUN pip install --no-cache-dir .
COPY config.yaml ./
COPY rules ./rules
COPY roles ./roles
COPY topics ./topics
COPY knowledge ./knowledge          # 新增
```

- 在 CI 或脚本中加 `docker build` 冒烟（daemon 可用时），防止资产清单再次漂移。
- 可选：启动时（lifespan）预载一次 corpus，把缺失提前到进程启动而非首场比赛。

## 验收标准

- `docker build` 成功；容器启动后创建一场 lite 比赛能正常进入 running 并跑完（E2E 冒烟）。
- 故意删掉 `knowledge/` 重建镜像时，失败发生在构建期或进程启动期，而非用户请求期。
