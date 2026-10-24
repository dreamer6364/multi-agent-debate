"""开发档 Web 服务器：FakeLLM 剧本回放 + 确定性摘要，供前端联调（不出网、零 token）。

用法：python scripts/dev_uvicorn.py [port]
可选环境变量 DEV_DB_URL：注入独立数据库（E2E 隔离用）。
"""
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "src"))
sys.path.insert(0, str(BACKEND_ROOT))  # 使 tests.fakes 可导入（开发档专用，CONSTRAINTS B 系不涉及）

import json  # noqa: E402

import uvicorn  # noqa: E402

from debate.api.app import create_app  # noqa: E402
from debate.core.config import deep_merge  # noqa: E402
from debate.engine.llm.gateway import Gateway  # noqa: E402
from tests.fakes.compressor import fake_columns  # noqa: E402
from tests.fakes.llm import FakeLLM  # noqa: E402


def gateway_factory(config, fmt):
    script = BACKEND_ROOT / "tests" / "fakes" / f"script_{fmt.value}.json"
    backend = FakeLLM.scripted(script)
    backend.stream_delay = float(os.environ.get("DEV_STREAM_DELAY", "0.04"))  # 打字节奏（演示流式直播）
    return Gateway(backend=backend, model_debater="fake",
                   model_official="fake", max_retries=1, sleep=lambda s: None)


# 应用剧本自带的配置覆盖（自由辩时长等），保证轮次与剧本对齐（同 CLI --fake）
_script_meta = json.loads((BACKEND_ROOT / "tests" / "fakes" / "script_lite.json").read_text(encoding="utf-8")).get("meta", {})
_overrides = _script_meta.get("config_overrides", {})

app = create_app(
    config_path=BACKEND_ROOT / "config.yaml",
    db_url=os.environ.get("DEV_DB_URL"),  # 默认跟随 .env 的 MySQL；E2E 注入 mad_e2e 隔离库
    gateway_factory=gateway_factory,
    compressor_factory=lambda config, gateway: fake_columns,
    config_overrides=_overrides or None,
)

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
