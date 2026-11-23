"""Contract tests for the frontends and the config renderer they share.

These verify the seam between the shared engine/config and each service: the CLI,
the config-file renderer (used by `init` and the web builder), and the MCP server.
"""
import contextlib
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

import elenchus.cli as cli
from elenchus.configio import render_config
from elenchus.core.checks import get_check
from elenchus.core.config import CheckConfig, Config, load_config

_ENV_KEYS = ["LLM_PROVIDER", "LLM_MODEL", "LLM_EFFORT", "FALLACY_SENSITIVITY", "RATE_LIMIT_PER_MIN"]


@contextlib.contextmanager
def clean_env(**overrides):
    """Run with the elenchus env vars cleared (then optional overrides set)."""
    saved = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
    for k, v in overrides.items():
        os.environ[k] = v
    try:
        yield
    finally:
        for k in overrides:
            os.environ.pop(k, None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


# ---- config renderer <-> loader contract ---------------------------------

def test_rendered_config_is_valid_yaml_and_round_trips():
    text = render_config()
    data = yaml.safe_load(text)  # must parse
    assert data["provider"] and "fallacy" in data["checks"]
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        with clean_env():
            cfg = load_config(path)
            assert len(cfg.enabled_techniques(get_check("fallacy"))) == len(get_check("fallacy").techniques)
    finally:
        os.unlink(path)


def test_rendered_config_reflects_a_toggle():
    text = render_config(Config(checks={"fallacy": CheckConfig(techniques={"ad_hominem": False})}))
    line = next(ln for ln in text.splitlines() if ln.strip().startswith("ad_hominem:"))
    assert "false" in line


# ---- CLI ------------------------------------------------------------------

def test_cli_init_and_checks():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "elenchus.yaml")
    assert cli.main(["init", "--path", path]) == 0 and os.path.exists(path)
    assert cli.main(["init", "--path", path]) == 1  # refuses to overwrite without --force
    assert cli.main(["init", "--path", path, "--force"]) == 0
    assert cli.main(["checks", "--config", path]) == 0


def test_cli_analyze_json_stub():
    with clean_env(LLM_PROVIDER="stub"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["analyze", "--json", "A sufficiently long sample argument for the stub to analyze here."])
        assert rc == 0
        data = json.loads(buf.getvalue())
        assert "findings" in data and "overall" in data


def test_cli_analyze_all_checks_json():
    with clean_env(LLM_PROVIDER="stub"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["analyze", "--check", "all", "--json", "A sufficiently long sample argument to analyze across every check now."])
        assert rc == 0
        data = json.loads(buf.getvalue())
        assert {"fallacy", "rhetoric", "scam"} <= set(data)  # one result per enabled check


def test_cli_config_command():
    with clean_env(LLM_PROVIDER="stub"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["config"])
        out = buf.getvalue()
        assert rc == 0 and "provider" in out and "techniques" in out


# ---- web config builder ---------------------------------------------------

def test_web_config_from_payload_renders():
    from elenchus.frontends.web import _config_from_payload
    from elenchus.configio import render_config

    cfg = _config_from_payload({
        "provider": "openai", "model": "gpt-4o", "effort": "high", "sensitivity": "strict",
        "rate_limit_per_min": 3,
        "checks": {"fallacy": {"enabled": True, "techniques": {"ad_hominem": False}}},
    })
    assert cfg.provider == "openai" and cfg.checks["fallacy"].techniques["ad_hominem"] is False
    text = render_config(cfg)
    line = next(ln for ln in text.splitlines() if ln.strip().startswith("ad_hominem:"))
    assert "false" in line


def test_web_server_serves_page_and_renders():
    import json as _json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from elenchus.frontends import web

    html = web.PAGE.replace("__DATA__", _json.dumps(web._page_data())).encode()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web._make_handler(html))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert "Elenchus" in page and "config builder" in page
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/render",
            data=_json.dumps({"provider": "stub", "model": "stub",
                              "checks": {"fallacy": {"enabled": True, "techniques": {"ad_hominem": False}}}}).encode(),
            headers={"Content-Type": "application/json"},
        )
        out = _json.loads(urllib.request.urlopen(req, timeout=5).read())
        assert "yaml" in out and "ad_hominem:" in out["yaml"]
    finally:
        srv.shutdown()


# ---- serve (browser-extension API) ----------------------------------------

def _serve_server():
    import threading
    from http.server import ThreadingHTTPServer

    from elenchus.core.config import Config
    from elenchus.core.engine import Engine
    from elenchus.frontends.serve import make_handler

    engine = Engine(Config(provider="stub", rate_limit_per_min=0))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(engine))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _http(port, path, body=None, origin=None):
    import json as _json
    import urllib.error
    import urllib.request

    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=_json.dumps(body).encode() if body is not None else None,
        headers=headers,
    )
    try:
        res = urllib.request.urlopen(req, timeout=5)
        return res.status, _json.loads(res.read())
    except urllib.error.HTTPError as e:
        return e.code, _json.loads(e.read())


def test_serve_health_and_analyze():
    srv, port = _serve_server()
    try:
        code, health = _http(port, "/api/health")
        assert code == 200 and health["ok"] and any(c["id"] == "fallacy" for c in health["checks"])
        code, out = _http(port, "/api/analyze", {"text": "A sufficiently long sample argument to analyze."})
        assert code == 200 and out["results"][0]["check"] == "fallacy"
        assert "findings" in out["results"][0]
        code, out = _http(port, "/api/analyze", {"text": "x" * 40, "check": "nope"})
        assert code == 400
    finally:
        srv.shutdown()


def test_serve_rate_limit_and_cache():
    import threading
    from http.server import ThreadingHTTPServer

    from elenchus.core.config import Config
    from elenchus.core.engine import Engine
    from elenchus.frontends import serve as serve_mod
    from elenchus.frontends.serve import make_handler

    serve_mod._cache.clear()
    engine = Engine(Config(provider="stub", rate_limit_per_min=1))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(engine))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        text_a = "The first sufficiently long sample argument to analyze."
        code, _ = _http(port, "/api/analyze", {"text": text_a})
        assert code == 200
        # Same text again: served from cache, no rate-limit slot consumed.
        code, _ = _http(port, "/api/analyze", {"text": text_a})
        assert code == 200
        # New text: the single slot is spent → 429 with retry_after.
        code, out = _http(port, "/api/analyze", {"text": "A different long argument that is not cached yet."})
        assert code == 429 and out["retry_after"] > 0
    finally:
        srv.shutdown()
        serve_mod._cache.clear()


def test_serve_blocks_web_page_origins():
    srv, port = _serve_server()
    try:
        code, out = _http(port, "/api/analyze", {"text": "x" * 40}, origin="https://evil.example")
        assert code == 403
        code, _ = _http(port, "/api/analyze", {"text": "A sufficiently long sample argument to analyze."},
                        origin="chrome-extension://abcdefg")
        assert code == 200
        code, _ = _http(port, "/api/analyze", {"text": "A sufficiently long sample argument to analyze."},
                        origin="http://localhost:9999")  # same-machine dev pages are trusted
        assert code == 200
        code, out = _http(port, "/api/analyze", {"text": "x" * 40}, origin="http://localhost.evil.example")
        assert code == 403
    finally:
        srv.shutdown()


# ---- MCP server -----------------------------------------------------------

def test_mcp_server_registers_tools():
    try:
        from elenchus.frontends.mcp_server import build_server
    except ImportError:
        return  # mcp extra not installed
    import asyncio

    server = build_server()
    tools = asyncio.new_event_loop().run_until_complete(server.list_tools())
    assert {"analyze", "list_checks"} <= {t.name for t in tools}


if __name__ == "__main__":
    tests = sorted((k, v) for k, v in globals().items() if k.startswith("test_") and callable(v))
    for name, fn in tests:
        fn()
        print("PASS", name)
    print(f"\nOK — {len(tests)} tests passed")
