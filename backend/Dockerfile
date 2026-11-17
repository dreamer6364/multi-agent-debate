FROM python:3.12-slim

WORKDIR /app

# ISS-004：README.md* 通配无匹配文件会直接导致构建失败（no source files were specified），
# 故只 COPY 实际存在的 pyproject.toml；knowledge/ 是 RAG 证据库（rag.enabled 默认开），
# 缺它则容器内每场比赛都 CorpusError fail-fast，必须随镜像打包。
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Prompt 资产与配置（运行时由 create_app 从 config.yaml 所在目录加载）
COPY config.yaml ./
COPY rules ./rules
COPY roles ./roles
COPY topics ./topics
COPY knowledge ./knowledge

# 迁移资产（ISS-014 B-3）：生产启动先 upgrade 再起服务——create_all 对已存在表
# 不会加列，表结构变更必须走 Alembic（CONSTRAINTS V4）。空库由迁移完成全部建表；
# 历史 create_all 库迁移已幂等化（版本对齐，不炸“表已存在”）。
COPY alembic.ini ./
COPY alembic ./alembic

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn debate.api.app:create_app --factory --host 0.0.0.0 --port 8000"]
