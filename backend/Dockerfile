FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md* ./
COPY src ./src
RUN pip install --no-cache-dir .

# Prompt 资产与配置（运行时由 create_app 从 config.yaml 所在目录加载）
COPY config.yaml ./
COPY rules ./rules
COPY roles ./roles
COPY topics ./topics

EXPOSE 8000
CMD ["uvicorn", "debate.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
