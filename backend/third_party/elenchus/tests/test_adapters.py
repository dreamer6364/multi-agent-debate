"""Adapter rendering tests. Skips gracefully if discord/slack aren't installed."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_SIGNING_SECRET", "test-secret")

try:
    from elenchus.frontends.discord import build_embed
    from elenchus.frontends.slack import format_result
    from elenchus.core.checks import get_check

    HAVE = True
    SKIP_REASON = ""
except Exception as exc:  # discord.py / slack_bolt not installed
    HAVE = False
    SKIP_REASON = str(exc)

CANNED = {
    "overall": "Attacks the person instead of the claim.",
    "findings": [
        {"name": "Ad hominem", "quote": "not even a scientist",
         "explanation": "Rejects the claim based on the person.", "confidence": "high"},
    ],
}


def test_discord_build_embed_has_fields():
    if not HAVE:
        print("SKIP (adapters unavailable):", SKIP_REASON)
        return
    embed = build_embed(CANNED, get_check("fallacy"))
    assert embed.title == "🧠 Fallacy check"
    assert len(embed.fields) == 1 and embed.fields[0].name.startswith("Ad hominem")


def test_discord_empty_case():
    if not HAVE:
        return
    embed = build_embed({"overall": "", "findings": []}, get_check("scam"))
    assert len(embed.fields) == 0 and embed.title.startswith("🚩")


def test_slack_format_result():
    if not HAVE:
        return
    text = format_result(CANNED, get_check("fallacy"))
    assert "Ad hominem" in text and "> not even a scientist" in text


if __name__ == "__main__":
    tests = sorted((k, v) for k, v in globals().items() if k.startswith("test_") and callable(v))
    for name, fn in tests:
        fn()
        print("PASS", name)
    print(f"\nOK — {len(tests)} tests ran" + ("" if HAVE else " (adapters skipped)"))
