"""Configuration: defaults <- config file <- environment (env wins).

Resolution order for a config file (first found):
  1. explicit path argument
  2. $ELENCHUS_CONFIG
  3. ./elenchus.yaml (or .yml)
  4. ~/.config/elenchus/config.yaml

API keys are ALWAYS read from the environment, never from the config file, so a
config file is safe to commit and share.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .checks import Check
from .prompt import normalize_sensitivity

try:  # optional convenience — load a local .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

import yaml

_DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-4o",
    "google": "gemini-2.0-flash",
    "stub": "stub",
}


@dataclass
class CheckConfig:
    enabled: bool = True
    techniques: dict[str, bool] = field(default_factory=dict)  # technique id -> on/off override


@dataclass
class Config:
    provider: str = "anthropic"
    model: str = "claude-opus-4-8"
    effort: str = "medium"
    sensitivity: str = "balanced"
    rate_limit_per_min: int = 5
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    google_api_key: str | None = None
    checks: dict[str, CheckConfig] = field(default_factory=dict)

    def check_enabled(self, check_id: str) -> bool:
        cc = self.checks.get(check_id)
        return cc.enabled if cc else True

    def enabled_techniques(self, check: Check) -> list:
        """Return the check's techniques that are on under this config (allow-list)."""
        cc = self.checks.get(check.id)
        out = []
        for t in check.techniques:
            on = t.default_enabled
            if cc and t.id in cc.techniques:
                on = cc.techniques[t.id]
            if on:
                out.append(t)
        return out


_CONFIG_NAMES = ("elenchus.yaml", "elenchus.yml")


def find_config(explicit: str | os.PathLike | None = None) -> Path | None:
    if explicit:
        return Path(explicit)
    env = os.getenv("ELENCHUS_CONFIG")
    if env:
        return Path(env)
    for name in _CONFIG_NAMES:
        p = Path(name)
        if p.exists():
            return p
    home = Path.home() / ".config" / "elenchus" / "config.yaml"
    return home if home.exists() else None


def _parse_checks(raw) -> dict[str, CheckConfig]:
    result: dict[str, CheckConfig] = {}
    for cid, c in (raw or {}).items():
        if isinstance(c, bool):
            result[cid] = CheckConfig(enabled=c)
        elif isinstance(c, dict):
            result[cid] = CheckConfig(
                enabled=bool(c.get("enabled", True)),
                techniques={k: bool(v) for k, v in (c.get("techniques") or {}).items()},
            )
    return result


def load_config(path: str | os.PathLike | None = None) -> Config:
    data: dict = {}
    cfg_file = find_config(path)
    if cfg_file and cfg_file.exists():
        data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    elif path:  # an explicit --config that doesn't exist is a mistake, not a silent default
        import sys

        print(f"Warning: config file '{path}' not found; using defaults + environment.", file=sys.stderr)

    # env wins over file wins over default
    provider = (os.getenv("LLM_PROVIDER") or data.get("provider") or "anthropic").lower()
    model = os.getenv("LLM_MODEL") or data.get("model") or _DEFAULT_MODELS.get(provider, "gpt-4o")
    effort = (os.getenv("LLM_EFFORT") or data.get("effort") or "medium").lower()
    sensitivity = normalize_sensitivity(os.getenv("FALLACY_SENSITIVITY") or data.get("sensitivity"))
    # 0 is a valid value (disables), so don't use `or` — check for None explicitly.
    rate_raw = os.getenv("RATE_LIMIT_PER_MIN")
    if rate_raw is None:
        rate_raw = data.get("rate_limit_per_min")
    try:
        rate = 5 if rate_raw is None else int(rate_raw)
    except (TypeError, ValueError):
        rate = 5

    return Config(
        provider=provider,
        model=model,
        effort=effort,
        sensitivity=sensitivity,
        rate_limit_per_min=rate,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_base_url=os.getenv("OPENAI_BASE_URL") or None,
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        checks=_parse_checks(data.get("checks")),
    )
