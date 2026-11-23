"""Render an elenchus config file as commented YAML.

Shared by `elenchus init` and the web config builder so the generated file always
lists every current check and technique, with comments aligned into columns.
"""
from __future__ import annotations

from .core.checks import CHECKS
from .core.config import Config

PROVIDERS = ["anthropic", "openai", "google", "stub"]
SENSITIVITIES = ["lenient", "balanced", "strict"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]


def _b(v: bool) -> str:
    return "true" if v else "false"


def _commented(left: str, comment: str, col: int) -> str:
    """`left`, padded to column `col`, then `# comment`."""
    return f"{left}{' ' * max(col - len(left), 1)}# {comment}"


def render_config(cfg: Config | None = None) -> str:
    cfg = cfg or Config()

    settings = [
        (f"provider: {cfg.provider}", "anthropic | openai | google | stub"),
        (f"model: {cfg.model}", None),
        (f"effort: {cfg.effort}", "low | medium | high | xhigh | max"),
        (f"sensitivity: {cfg.sensitivity}", "lenient | balanced | strict"),
        (f"rate_limit_per_min: {cfg.rate_limit_per_min}", "per user; <=0 disables"),
    ]
    scol = max(len(left) for left, _ in settings) + 2

    out = [
        "# elenchus configuration",
        "# API keys come from the environment (ANTHROPIC_API_KEY / OPENAI_API_KEY /",
        "# GOOGLE_API_KEY) — never from this file, so it is safe to commit and share.",
        "# Any value here can be overridden at runtime by the matching env var.",
        "",
    ]
    out += [left if comment is None else _commented(left, comment, scol) for left, comment in settings]
    out += ["", "checks:"]

    # Align comment columns across the whole file, not just within one check.
    hdr_col = max(len(f"  {cid}:") for cid in CHECKS) + 2
    tid_col = max(len(t.id) for c in CHECKS.values() for t in c.techniques) + 1  # + the colon

    for cid, check in CHECKS.items():
        cc = cfg.checks.get(cid)
        enabled = cc.enabled if cc else True
        out.append(_commented(f"  {cid}:", check.title, hdr_col))
        out.append(f"    enabled: {_b(enabled)}")
        out.append("    techniques:")
        for t in check.techniques:
            on = cc.techniques[t.id] if cc and t.id in cc.techniques else t.default_enabled
            left = f"      {(t.id + ':'):<{tid_col}} {_b(on):<5}"
            out.append(f"{left}  # {t.name}")
    return "\n".join(out) + "\n"
