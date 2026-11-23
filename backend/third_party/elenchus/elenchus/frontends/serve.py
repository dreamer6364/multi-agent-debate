"""Localhost JSON API — the bridge for the browser extension.

`elenchus serve` binds a stdlib HTTP server to 127.0.0.1 and exposes the shared
engine to same-machine callers:

    GET  /api/health              resolved config + available checks
    POST /api/analyze             {"text", "check"?, "sensitivity"?} — check may be "all"

Requests carrying a web-page Origin are refused so a site you happen to visit
can't spend your tokens through the local port. Allowed callers: the Chrome
extension (chrome-extension:// origin), same-machine pages (http://127.0.0.1 /
http://localhost — used by the extension's dev/QA harness), and curl (no Origin).
"""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8474
_RATE_KEY = "local"  # single-user server: one rate-limit bucket
_CACHE_MAX = 256  # analyses kept for repeat requests (page reloads, re-checks)

_cache: OrderedDict[tuple, dict] = OrderedDict()
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        result = _cache.get(key)
        if result is not None:
            _cache.move_to_end(key)
        return result


def _cache_put(key, result) -> None:
    with _cache_lock:
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def _analyze_payload(engine, payload: dict) -> tuple[int, dict]:
    from ..core.checks import CHECKS, DEFAULT_CHECK, get_check
    from ..core.errors import friendly_error

    text = str(payload.get("text") or "").strip()
    if not text:
        return 400, {"error": "No text to analyze."}
    check = payload.get("check") or DEFAULT_CHECK
    if check != "all" and check not in CHECKS:
        return 400, {"error": f"Unknown check '{check}'. Choose from: {', '.join(CHECKS)}, all."}
    sensitivity = payload.get("sensitivity") or None

    if check == "all":
        check_ids = [cid for cid in CHECKS if engine.config.check_enabled(cid)]
    else:
        check_ids = [check]

    results = []
    for cid in check_ids:
        key = (cid, sensitivity or engine.config.sensitivity, text)
        result = _cache_get(key)
        if result is None:  # cache hits don't consume rate-limit slots
            allowed, retry_after = engine.limiter.check(_RATE_KEY)
            if not allowed:
                return 429, {"error": "Rate limit reached.", "retry_after": round(retry_after, 1)}
            try:
                result = engine.analyze(text, cid, sensitivity)
            except Exception as exc:  # noqa: BLE001 — surface a friendly message, never a traceback
                return 502, {"error": friendly_error(exc)}
            _cache_put(key, result)
        chk = get_check(cid)
        results.append({"check": cid, "title": chk.title, "empty_msg": chk.empty_msg, **result})
    return 200, {"results": results}


def _health_payload(engine) -> dict:
    from .. import __version__
    from ..core.checks import CHECKS, DEFAULT_CHECK

    cfg = engine.config
    return {
        "ok": True,
        "version": __version__,
        "provider": cfg.provider,
        "model": cfg.model,
        "sensitivity": cfg.sensitivity,
        "default_check": DEFAULT_CHECK,
        "checks": [
            {"id": cid, "title": c.title, "enabled": cfg.check_enabled(cid)}
            for cid, c in CHECKS.items()
        ],
    }


def _trusted_origin(origin: str) -> bool:
    """chrome-extension:// and same-machine http pages; never the open web.

    ELENCHUS_TRUST_ORIGINS (comma-separated exact origins) can extend the list —
    a dev escape hatch for testing the content script injected into a real site.
    """
    import os

    if origin.startswith("chrome-extension://"):
        return True
    for base in ("http://127.0.0.1", "http://localhost"):
        if origin == base or origin.startswith(base + ":"):
            return True
    extra = os.environ.get("ELENCHUS_TRUST_ORIGINS", "")
    return origin in {o.strip() for o in extra.split(",") if o.strip()}


def make_handler(engine):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):  # quiet
            pass

        def _origin_blocked(self) -> bool:
            origin = self.headers.get("Origin", "")
            return origin.startswith(("http://", "https://")) and not _trusted_origin(origin)

        def _cors(self) -> None:
            origin = self.headers.get("Origin", "")
            if _trusted_origin(origin):
                self.send_header("Access-Control-Allow-Origin", origin)

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):  # CORS preflight, in case a fetch path needs it
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            if _trusted_origin(self.headers.get("Origin", "")):
                # Chrome Private Network Access: public https pages (the dev
                # escape hatch) need this to reach a loopback server.
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if self._origin_blocked():
                self._send(403, {"error": "Web-page origins are not allowed."})
            elif self.path == "/api/health":
                self._send(200, _health_payload(engine))
            else:
                self._send(404, {"error": "Not found."})

        def do_POST(self):
            if self._origin_blocked():
                self._send(403, {"error": "Web-page origins are not allowed."})
                return
            if self.path != "/api/analyze":
                self._send(404, {"error": "Not found."})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                self._send(400, {"error": "Body must be a JSON object."})
                return
            code, body = _analyze_payload(engine, payload)
            self._send(code, body)

    return Handler


def serve(port: int = DEFAULT_PORT, config_path=None) -> None:
    from ..core.config import load_config
    from ..core.engine import Engine

    engine = Engine(load_config(config_path))
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(engine))
    cfg = engine.config
    print(f"Elenchus API → http://127.0.0.1:{port}/   (Ctrl-C to stop)")
    print(f"  provider={cfg.provider} model={cfg.model or '(default)'} sensitivity={cfg.sensitivity} rate={cfg.rate_limit_per_min}/min")
    print("  The Chrome extension talks to this server — see chrome-extension/README.md")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        server.shutdown()
