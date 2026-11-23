"""The analysis engine: a Config becomes a provider + rate limiter + analyze()."""
from __future__ import annotations

from .checks import DEFAULT_CHECK, get_check
from .config import Config, load_config
from .prompt import build_system, build_user
from .providers import MalformedOutput, build_provider
from .ratelimit import RateLimiter
from .schema import FINDINGS_SCHEMA

MIN_INPUT_CHARS = 15
MAX_INPUT_CHARS = 6000
_VALID_CONFIDENCE = {"low", "medium", "high"}


def normalize(result) -> dict:
    """Coerce/validate model output into a safe, well-typed result.

    Tolerant of junk (skips malformed findings, fixes bad confidence) but raises
    MalformedOutput if the top-level shape is unusable.
    """
    if not isinstance(result, dict):
        raise MalformedOutput("Model output was not an object.")
    overall = result.get("overall")
    overall = overall if isinstance(overall, str) else ""

    findings = []
    for item in result.get("findings") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        explanation = str(item.get("explanation", "")).strip()
        if not name and not explanation:
            continue
        conf = item.get("confidence")
        findings.append(
            {
                "name": name or "Finding",
                "quote": str(item.get("quote", "")).strip(),
                "explanation": explanation,
                "confidence": conf if conf in _VALID_CONFIDENCE else "medium",
            }
        )
    return {"overall": overall, "findings": findings}


class Engine:
    """Holds resolved config, a provider, and a rate limiter. Frontends share one."""

    def __init__(self, config: Config | None = None, provider=None):
        self.config = config or load_config()
        self.provider = provider if provider is not None else build_provider(self.config)
        self.limiter = RateLimiter(self.config.rate_limit_per_min)

    def analyze(self, text: str, check: str | None = None, sensitivity: str | None = None) -> dict:
        """Return {"overall": str, "findings": [ {name, quote, explanation, confidence} ]}."""
        text = (text or "").strip()
        if len(text) < MIN_INPUT_CHARS:
            return {"overall": "That's too short to analyze — send a longer message or argument.", "findings": []}
        if len(text) > MAX_INPUT_CHARS:
            text = text[:MAX_INPUT_CHARS]

        chk = get_check(check or DEFAULT_CHECK)
        if not self.config.check_enabled(chk.id):
            return {"overall": f"The '{chk.id}' check is disabled in this configuration.", "findings": []}
        techniques = self.config.enabled_techniques(chk)
        if not techniques:
            return {"overall": f"No techniques are enabled for the '{chk.id}' check.", "findings": []}

        system = build_system(chk.framing, chk.noun, techniques, sensitivity or self.config.sensitivity)
        result = self.provider.analyze(system, build_user(text), FINDINGS_SCHEMA, self.config.effort)
        return normalize(result)
