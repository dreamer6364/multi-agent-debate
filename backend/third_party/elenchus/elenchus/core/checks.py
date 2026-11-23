"""Checks and their techniques — loaded from data/checks/*.yaml.

Each check is a named analysis mode (fallacy, scam, ...) with a list of individually
addressable techniques. Adding a check = a new YAML file listed in checks/index.json;
adding a technique = a new list entry. No code change required.
"""
from __future__ import annotations

from dataclasses import dataclass

from .resources import read_json, read_yaml


@dataclass(frozen=True)
class Technique:
    id: str
    name: str
    definition: str
    default_enabled: bool = True


@dataclass(frozen=True)
class Check:
    id: str
    title: str  # shown as the embed / message title
    noun: str  # filled into the sensitivity template
    empty_msg: str  # shown when findings is empty
    framing: str  # the check-specific system-prompt preamble
    techniques: tuple[Technique, ...]

    def technique(self, tid: str) -> Technique | None:
        return next((t for t in self.techniques if t.id == tid), None)


def _load_check(rel: str) -> Check:
    data = read_yaml(rel)
    techniques = tuple(
        Technique(
            id=t["id"],
            name=t["name"],
            definition=t["definition"],
            default_enabled=t.get("enabled", True),
        )
        for t in data.get("techniques", [])
    )
    noun = data["noun"]
    return Check(
        id=data["id"],
        title=data["title"],
        noun=noun,
        empty_msg=data.get("empty_msg", f"No clear {noun} found."),
        framing=data["framing"],
        techniques=techniques,
    )


_INDEX = read_json("checks/index.json")
DEFAULT_CHECK: str = _INDEX["default"]

CHECKS: dict[str, Check] = {}
for _rel in _INDEX["checks"]:
    _c = _load_check(_rel)
    CHECKS[_c.id] = _c


def get_check(check_id: str | None) -> Check:
    return CHECKS.get(check_id or "", CHECKS[DEFAULT_CHECK])
