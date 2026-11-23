"""Elenchus command-line interface.

    elenchus analyze "text"            # check text (pretty output; --json for raw)
    elenchus checks                    # list checks + techniques and their on/off state
    elenchus init                      # write a commented elenchus.yaml
    elenchus web                       # open the visual config builder
    elenchus serve                     # run the localhost API (browser extension)
    elenchus mcp                       # run the MCP server (stdio)
    elenchus discord | slack           # run a chat bot
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__

# ANSI helpers (auto-disabled when not a TTY)
_TTY = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def _engine(config_path=None):
    from .core.config import load_config
    from .core.engine import Engine

    return Engine(load_config(config_path))


# ---- commands ------------------------------------------------------------

def cmd_analyze(args) -> int:
    text = " ".join(args.text).strip() or sys.stdin.read().strip()
    if not text:
        print("Nothing to analyze. Pass text or pipe it via stdin.", file=sys.stderr)
        return 2
    from .core.checks import CHECKS, DEFAULT_CHECK, get_check
    from .core.errors import friendly_error

    if args.check and args.check != "all" and args.check not in CHECKS:
        print(f"Unknown check '{args.check}'. Choose from: {', '.join(CHECKS)}, all.", file=sys.stderr)
        return 2

    engine = _engine(args.config)
    if args.check == "all":
        check_ids = [cid for cid in CHECKS if engine.config.check_enabled(cid)]
    else:
        check_ids = [args.check or DEFAULT_CHECK]

    results = {}
    for cid in check_ids:
        try:
            results[cid] = engine.analyze(text, cid, args.sensitivity)
        except Exception as exc:  # noqa: BLE001
            print(f"Error ({cid}): {friendly_error(exc)}", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(results if args.check == "all" else results[check_ids[0]], indent=2))
        return 0
    for cid in check_ids:
        _print_result(results[cid], get_check(cid))
    return 0


def _print_result(result: dict, check) -> None:
    findings = result.get("findings") or []
    print(_c("1", check.title))
    overall = (result.get("overall") or "").strip()
    if overall:
        print(overall)
    color = "33" if findings else "32"
    for f in findings:
        conf = f.get("confidence", "")
        head = _c(color, f"\n  ▸ {f.get('name', 'Finding')}") + (_c("2", f"  ({conf})") if conf else "")
        print(head)
        if f.get("quote"):
            print(_c("2", f'    "{f["quote"]}"'))
        if f.get("explanation"):
            print(f"    {f['explanation']}")
    if not findings:
        print(_c("32", "✓ nothing flagged"))


def cmd_checks(args) -> int:
    from .core.checks import CHECKS
    from .core.config import load_config

    cfg = load_config(args.config)
    for cid, check in CHECKS.items():
        state = "enabled" if cfg.check_enabled(cid) else "disabled"
        print(_c("1", f"\n{cid}") + f"  {check.title}  [{state}]")
        enabled_ids = {t.id for t in cfg.enabled_techniques(check)}
        for t in check.techniques:
            on = t.id in enabled_ids
            mark = _c("32", "✓") if on else _c("31", "✗")
            print(f"  {mark} {t.id:<22} {t.name}")
    return 0


def cmd_config(args) -> int:
    from .core.checks import CHECKS
    from .core.config import find_config, load_config

    cfg = load_config(args.config)
    src = find_config(args.config)
    print(_c("1", "Resolved configuration"))
    print(f"  config file : {src or '(none — defaults + environment)'}")
    print(f"  provider    : {cfg.provider}")
    print(f"  model       : {cfg.model}")
    print(f"  effort      : {cfg.effort}")
    print(f"  sensitivity : {cfg.sensitivity}")
    disabled = "  (disabled)" if cfg.rate_limit_per_min <= 0 else ""
    print(f"  rate limit  : {cfg.rate_limit_per_min}/min{disabled}")
    if cfg.provider != "stub":
        key = {"anthropic": cfg.anthropic_api_key, "openai": cfg.openai_api_key, "google": cfg.google_api_key}.get(cfg.provider)
        env = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "google": "GOOGLE_API_KEY"}.get(cfg.provider, "")
        print(f"  api key     : {_c('32', 'set') if key else _c('31', f'MISSING — set {env}')}")
    print(_c("1", "\nChecks"))
    for cid, check in CHECKS.items():
        on = len(cfg.enabled_techniques(check))
        state = "enabled " if cfg.check_enabled(cid) else "disabled"
        print(f"  {cid:<10} {state}  {on}/{len(check.techniques)} techniques")
    return 0


def cmd_init(args) -> int:
    from .configio import render_config
    from .core.config import load_config

    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite).", file=sys.stderr)
        return 1
    # Seed from an existing config if one is discoverable, else defaults.
    cfg = load_config(args.config) if args.config else None
    path.write_text(render_config(cfg), encoding="utf-8")
    print(f"Wrote {path}")
    print("Edit it, or run `elenchus web` for a visual builder.")
    return 0


def cmd_web(args) -> int:
    from .frontends.web import serve

    serve(port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_serve(args) -> int:
    from .frontends.serve import serve

    serve(port=args.port, config_path=args.config)
    return 0


def cmd_mcp(args) -> int:
    from .frontends.mcp_server import main as mcp_main

    mcp_main(config_path=args.config)
    return 0


def cmd_discord(args) -> int:
    from .frontends.discord import main as discord_main

    discord_main(config_path=args.config)
    return 0


def cmd_slack(args) -> int:
    from .frontends.slack import main as slack_main

    slack_main(config_path=args.config)
    return 0


# ---- parser --------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="elenchus", description="Cross-examine text for logical fallacies and manipulation.")
    p.add_argument("--version", action="version", version=f"elenchus {__version__}")
    sub = p.add_subparsers(dest="command")

    def add_config(sp):
        sp.add_argument("--config", metavar="PATH", help="Config file (default: auto-discover elenchus.yaml)")

    a = sub.add_parser("analyze", help="Check a piece of text")
    a.add_argument("text", nargs="*", help="Text to analyze (or pipe via stdin)")
    a.add_argument("--check", default=None, help="Which check to run: fallacy | rhetoric | scam | all (default: fallacy)")
    a.add_argument("--sensitivity", choices=["lenient", "balanced", "strict"], default=None)
    a.add_argument("--json", action="store_true", help="Raw JSON output")
    add_config(a)
    a.set_defaults(func=cmd_analyze)

    c = sub.add_parser("checks", help="List checks and techniques")
    add_config(c)
    c.set_defaults(func=cmd_checks)

    cf = sub.add_parser("config", help="Show the resolved configuration")
    add_config(cf)
    cf.set_defaults(func=cmd_config)

    i = sub.add_parser("init", help="Write a commented config file")
    i.add_argument("--path", default="elenchus.yaml", help="Where to write (default: ./elenchus.yaml)")
    i.add_argument("--force", action="store_true", help="Overwrite if it exists")
    add_config(i)
    i.set_defaults(func=cmd_init)

    w = sub.add_parser("web", help="Open the visual config builder")
    w.add_argument("--port", type=int, default=8765)
    w.add_argument("--no-browser", action="store_true", help="Don't auto-open a browser")
    w.set_defaults(func=cmd_web)

    sv = sub.add_parser("serve", help="Run the localhost API for the browser extension")
    sv.add_argument("--port", type=int, default=8474)
    add_config(sv)
    sv.set_defaults(func=cmd_serve)

    m = sub.add_parser("mcp", help="Run the MCP server (stdio)")
    add_config(m)
    m.set_defaults(func=cmd_mcp)

    d = sub.add_parser("discord", help="Run the Discord bot")
    add_config(d)
    d.set_defaults(func=cmd_discord)

    s = sub.add_parser("slack", help="Run the Slack bot")
    add_config(s)
    s.set_defaults(func=cmd_slack)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
