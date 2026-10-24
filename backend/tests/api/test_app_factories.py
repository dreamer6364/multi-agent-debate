"""create_app 默认工厂回归：服务层契约 (config, fmt) 必须被满足（生产路径曾被漏测）。"""
from pathlib import Path

import pytest

from debate.api.app import _default_compressor_factory, _default_gateway_factory
from debate.core.config import load_config, load_env
from debate.engine.llm.gateway import Gateway
from debate.engine.schema import DebateFormat


def test_default_gateway_factory_matches_service_contract(backend_root):
    env_file = backend_root / ".env"
    if env_file.exists():  # 生产由 create_app 加载；此处复现同一行为
        load_env(env_file)
    config = load_config(backend_root / "config.yaml")
    gateway = _default_gateway_factory(config, DebateFormat.LITE)
    assert isinstance(gateway, Gateway)
    assert gateway.model_debater  # 从 .env 读到模型名
    assert gateway.extra_body is not None and "thinking" in gateway.extra_body  # GLM 思考关闭透传


def test_default_compressor_factory_matches_service_contract(backend_root):
    config = load_config(backend_root / "config.yaml")
    build = _default_compressor_factory(config)
    gateway = _default_gateway_factory(config, DebateFormat.LITE)
    compressor = build(config, gateway)
    assert callable(compressor)
    assert compressor([]) == {}  # 空输入直接空五栏
