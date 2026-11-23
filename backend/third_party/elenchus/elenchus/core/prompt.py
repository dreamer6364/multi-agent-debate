"""Prompt assembly. Sensitivity templates come from data/sensitivity.json.

The system prompt is built from a check's framing plus the *enabled* techniques only
(a strict allow-list), so toggling a technique off actually removes it from the model's
instructions rather than merely de-emphasizing it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from .resources import read_json

if TYPE_CHECKING:
    from .checks import Technique

_S = read_json("sensitivity.json")
_LEVELS: dict[str, str] = _S["levels"]

DEFAULT_SENSITIVITY = _S["default"]
SENSITIVITIES = tuple(_LEVELS)


def normalize_sensitivity(value: str | None) -> str:
    value = (value or "").lower().strip()
    return value if value in _LEVELS else DEFAULT_SENSITIVITY


def build_system(framing: str, noun: str, techniques: "Sequence[Technique]", sensitivity: str | None = None) -> str:
    lines = [framing.strip(), ""]
    lines.append(f"Report ONLY {noun} from this list. Do NOT report anything not named here:")
    for t in techniques:
        lines.append(f"- {t.name}: {t.definition}")
    lines += ["", _LEVELS[normalize_sensitivity(sensitivity)].replace("{noun}", noun)]
    lines += ["", "Return only data matching the provided schema."]
    return "\n".join(lines)


def build_user(text: str) -> str:
    return (
        "Analyze the following message.\n\n"
        f"<message>\n{text}\n</message>"
    )
