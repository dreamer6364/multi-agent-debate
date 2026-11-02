"""create_app 默认工厂回归：服务层契约 (config, fmt) 必须被满足（生产路径曾被漏测）。"""

import pytest

from debate.api.app import _default_compressor_factory, _default_gateway_factory, create_app
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


async def test_cors_middleware_honors_allowlist(backend_root, monkeypatch):
    """ISS-008 回归：CORS_ORIGINS 显式配置才挂中间件——白名单放行、外来 origin 不发放、空值不挂。"""
    from httpx import ASGITransport, AsyncClient

    from .conftest import make_app

    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173, http://trusted.example")
    app = make_app(backend_root, None)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            allowed = await client.get("/api/health", headers={"Origin": "http://localhost:5173"})
            assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
            assert allowed.headers.get("access-control-allow-credentials") == "true"
            denied = await client.get("/api/health", headers={"Origin": "http://evil.example"})
            assert "access-control-allow-origin" not in denied.headers
            preflight = await client.options(
                "/api/debates",
                headers={"Origin": "http://trusted.example",
                         "Access-Control-Request-Method": "POST"},
            )
            assert preflight.status_code == 200
            assert preflight.headers.get("access-control-allow-origin") == "http://trusted.example"

    monkeypatch.setenv("CORS_ORIGINS", "")  # 空值 = 同源部署，不挂中间件
    app2 = make_app(backend_root, None)
    async with app2.router.lifespan_context(app2):
        async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://t") as client:
            resp = await client.get("/api/health", headers={"Origin": "http://localhost:5173"})
            assert "access-control-allow-origin" not in resp.headers


def test_jwt_secret_prod_requires_explicit_value(backend_root, monkeypatch):
    """ISS-009 回归：生产档（DEBATE_ENV=prod）缺 JWT_SECRET 必须拒绝启动（fail-fast）。"""
    monkeypatch.setenv("JWT_SECRET", "")  # 空值触发"缺失"分支；load_env override=False 不会覆盖
    monkeypatch.setenv("DEBATE_ENV", "prod")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app(config_path=backend_root / "config.yaml")
    # 显式提供则正常启动
    monkeypatch.setenv("JWT_SECRET", "p" * 40)
    app = create_app(config_path=backend_root / "config.yaml")
    assert app.state.svc.jwt_secret == "p" * 40


def test_jwt_secret_dev_random_per_start(backend_root, monkeypatch):
    """ISS-009 回归：开发档缺 JWT_SECRET 不再用固定公开值——每次启动随机生成且互不相同。"""
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.delenv("DEBATE_ENV", raising=False)
    app1 = create_app(config_path=backend_root / "config.yaml")
    app2 = create_app(config_path=backend_root / "config.yaml")
    s1, s2 = app1.state.svc.jwt_secret, app2.state.svc.jwt_secret
    assert s1 != s2, "两次启动密钥相同，仍是固定值"
    assert len(s1) >= 32 and len(s2) >= 32
    assert "dev-insecure-secret" not in (s1, s2)
