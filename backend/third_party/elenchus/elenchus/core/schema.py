"""The structured-output schema, loaded from data/schema.json."""
from .resources import read_json

FINDINGS_SCHEMA = read_json("schema.json")
