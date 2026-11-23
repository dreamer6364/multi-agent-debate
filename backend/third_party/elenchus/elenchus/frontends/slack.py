"""Slack frontend (Socket Mode — no public URL needed).

Per check (fallacy, scam, ...):
  1. /fallacy <text>   /scam <text>             — slash commands
  2. Message shortcuts "Check Fallacies" / "Check for Scams"
     (callback_ids: check_fallacies / check_scams)
  3. DM the bot any text -> it runs the default check

The active config (elenchus.yaml) controls model, rate limit, and which checks run.
Set LLM_PROVIDER=stub to run with no API key.
Run via:  elenchus slack   (or python -m elenchus.frontends.slack)
"""
from __future__ import annotations

import logging
import os

from ..core.checks import CHECKS, DEFAULT_CHECK, Check, get_check
from ..core.config import load_config
from ..core.engine import Engine
from ..core.errors import friendly_error

log = logging.getLogger("elenchus.slack")

_engine: Engine | None = None  # set in main()


def format_result(result: dict, check: Check) -> str:
    findings = result.get("findings") or []
    overall = (result.get("overall") or "").strip()

    if not findings:
        return f"*{check.title}*\n{overall or check.empty_msg}"

    blocks = [f"*{check.title}*\n{overall}"]
    for f in findings[:10]:
        header = f"*{f.get('name', 'Finding')}*" + (f" · _{f['confidence']} confidence_" if f.get("confidence") else "")
        blocks.append(header + "\n" + (f"> {f['quote']}\n" if f.get("quote") else "") + f.get("explanation", ""))
    blocks.append("_Analyzes message structure, not who's right._")
    return "\n\n".join(blocks)


def _run(text: str, check_id: str) -> str:
    try:
        return format_result(_engine.analyze(text, check_id), get_check(check_id))
    except Exception as exc:  # noqa: BLE001
        log.exception("analysis failed (check=%s)", check_id)
        return f"⚠️ {friendly_error(exc)}"


def _rate_msg(user_id: str) -> str | None:
    ok, retry = _engine.limiter.check(user_id)
    return None if ok else f"⏳ You're going a bit fast — try again in {retry:.0f}s."


def _register(app) -> None:
    """Attach handlers for every check to a Slack Bolt app."""

    def make_command(check_id: str):
        def handler(ack, respond, command):
            ack()
            blocked = _rate_msg(command.get("user_id", ""))
            if blocked:
                respond(blocked)
                return
            text = (command.get("text") or "").strip()
            if not text:
                respond(f"Usage: `{command.get('command', '/fallacy')} <text to analyze>`")
                return
            respond(_run(text, check_id))

        return handler

    def make_shortcut(check_id: str):
        def handler(ack, shortcut, client):
            ack()
            user = (shortcut.get("user") or {}).get("id", "")
            channel = (shortcut.get("channel") or {}).get("id")
            blocked = _rate_msg(user)
            if blocked:
                client.chat_postEphemeral(channel=channel, user=user, text=blocked)
                return
            text = ((shortcut.get("message") or {}).get("text") or "").strip()
            if not text:
                return
            client.chat_postEphemeral(channel=channel, user=user, text=_run(text, check_id))

        return handler

    # One /<check> command + a check_<check> message shortcut per check.
    # (Add the matching command/shortcut in your Slack app config for each check.)
    for cid in CHECKS:
        app.command(f"/{cid}")(make_command(cid))
        app.shortcut(f"check_{cid}")(make_shortcut(cid))

    @app.event("message")
    def handle_dm(event, say):  # DM-chat mode only
        if event.get("channel_type") != "im" or event.get("subtype") or event.get("bot_id"):
            return
        text = (event.get("text") or "").strip()
        if not text:
            return
        blocked = _rate_msg(event.get("user", ""))
        say(blocked if blocked else _run(text, DEFAULT_CHECK))


def main(config_path: str | None = None) -> None:
    global _engine
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not bot_token or not app_token:
        raise SystemExit("Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN (Socket Mode) in your environment or .env.")

    _engine = Engine(load_config(config_path))
    app = App(token=bot_token, token_verification_enabled=False)
    _register(app)
    SocketModeHandler(app, app_token).start()


if __name__ == "__main__":
    main()
