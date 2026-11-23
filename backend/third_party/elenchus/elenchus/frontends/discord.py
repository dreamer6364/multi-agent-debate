"""Discord frontend — config-driven.

Per check (fallacy, scam, ...):
  1. /fallacy <text> [sensitivity]   /scam <text> [sensitivity]   — slash commands
  2. Right-click a message -> Apps -> "Check Fallacies" / "Check for Scams"
  3. DM the bot any text -> it runs the default check

Commands are user-installable and usable in guilds, DMs, and private channels. The
active config (elenchus.yaml) controls model, rate limit, and which checks/techniques
run; a disabled check replies that it's off. Set LLM_PROVIDER=stub to run with no key.

Run via:  elenchus discord   (or python -m elenchus.frontends.discord)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import discord
from discord import app_commands

from ..core.checks import CHECKS, DEFAULT_CHECK, Check, get_check
from ..core.config import load_config
from ..core.engine import Engine
from ..core.errors import friendly_error

log = logging.getLogger("elenchus.discord")

_engine: Engine | None = None  # set in main()

SENSITIVITY_CHOICES = [app_commands.Choice(name=s, value=s) for s in ("lenient", "balanced", "strict")]

intents = discord.Intents.default()
intents.message_content = True  # required for the DM-chat mode


class ElenchusClient(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync()


client = ElenchusClient()


def build_embed(result: dict, check: Check) -> discord.Embed:
    findings = result.get("findings") or []
    overall = (result.get("overall") or "").strip()

    if not findings:
        return discord.Embed(title=check.title, description=overall or check.empty_msg, color=0x2ECC71)

    embed = discord.Embed(title=check.title, description=overall[:4000] or "Findings:", color=0xE67E22)
    for f in findings[:10]:
        header = f.get("name", "Finding") + (f" · {f['confidence']} confidence" if f.get("confidence") else "")
        body = (f"> {f['quote']}\n" if f.get("quote") else "") + f.get("explanation", "")
        embed.add_field(name=header[:256], value=(body or "—")[:1024], inline=False)
    embed.set_footer(text="Analyzes message structure, not who's right.")
    return embed


def _rate_ok(user_id: str) -> tuple[bool, float]:
    return _engine.limiter.check(user_id)


async def _rate_limited(interaction: discord.Interaction) -> bool:
    ok, retry = _rate_ok(str(interaction.user.id))
    if not ok:
        await interaction.response.send_message(
            f"⏳ You're going a bit fast — try again in {retry:.0f}s.", ephemeral=True
        )
    return not ok


async def _respond(interaction: discord.Interaction, text: str, check_id: str, sensitivity: Optional[str]) -> None:
    if not (text or "").strip():
        await interaction.followup.send("There's no text to analyze.")
        return
    try:
        result = await asyncio.to_thread(_engine.analyze, text, check_id, sensitivity)
    except Exception as exc:  # noqa: BLE001
        log.exception("analysis failed (check=%s)", check_id)
        await interaction.followup.send(f"⚠️ {friendly_error(exc)}")
        return
    await interaction.followup.send(embed=build_embed(result, get_check(check_id)))


def _slash(name: str, description: str, check_id: str):
    @client.tree.command(name=name, description=description)
    @app_commands.describe(text="The message to analyze", sensitivity="How aggressively to flag")
    @app_commands.choices(sensitivity=SENSITIVITY_CHOICES)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def _cmd(interaction: discord.Interaction, text: str, sensitivity: Optional[app_commands.Choice[str]] = None):
        if await _rate_limited(interaction):
            return
        await interaction.response.defer(thinking=True)
        await _respond(interaction, text, check_id, sensitivity.value if sensitivity else None)

    return _cmd


def _context(name: str, check_id: str):
    @client.tree.context_menu(name=name)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def _ctx(interaction: discord.Interaction, message: discord.Message):
        if await _rate_limited(interaction):
            return
        await interaction.response.defer(thinking=True)
        await _respond(interaction, message.content or "", check_id, None)

    return _ctx


# One /<check> command and a right-click menu per check — new checks appear for free.
for _cid, _check in CHECKS.items():
    _slash(_cid, f"Check text with the {_cid} check", _cid)
    _context(f"Elenchus: {_cid}", _cid)


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or not isinstance(message.channel, discord.DMChannel):
        return
    content = (message.content or "").strip()
    if not content:
        return
    ok, retry = _rate_ok(str(message.author.id))
    if not ok:
        await message.channel.send(f"⏳ You're going a bit fast — try again in {retry:.0f}s.")
        return
    async with message.channel.typing():
        try:
            result = await asyncio.to_thread(_engine.analyze, content, DEFAULT_CHECK)
        except Exception as exc:  # noqa: BLE001
            log.exception("DM analysis failed")
            await message.channel.send(f"⚠️ {friendly_error(exc)}")
            return
    await message.channel.send(embed=build_embed(result, get_check(DEFAULT_CHECK)))


def main(config_path: str | None = None) -> None:
    global _engine
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _engine = Engine(load_config(config_path))
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("Set DISCORD_BOT_TOKEN in your environment or .env file.")
    client.run(token)


if __name__ == "__main__":
    main()
