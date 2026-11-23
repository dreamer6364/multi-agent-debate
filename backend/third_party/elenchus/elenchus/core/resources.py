"""Loader for packaged data (prompts, schema, sensitivity templates, check registry).

Data lives in `elenchus/data/` and is loaded via importlib.resources so it works
both from a source checkout and an installed wheel.
"""
from __future__ import annotations

import json
from importlib.resources import files

import yaml

_DATA = files("elenchus") / "data"


def read_text(rel: str) -> str:
    return (_DATA / rel).read_text(encoding="utf-8")


def read_json(rel: str):
    return json.loads(read_text(rel))


def read_yaml(rel: str):
    return yaml.safe_load(read_text(rel))
