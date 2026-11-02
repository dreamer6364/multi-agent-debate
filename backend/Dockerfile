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

EXPOSE 8000
CMD ["uvicorn", "debate.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
