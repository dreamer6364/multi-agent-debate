"""Core tests for the elenchus engine, config, checks, and providers.

Run under pytest with the package installed (`pip install -e .[all,dev]`), or
standalone: `python tests/test_core.py`.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elenchus.core.checks import CHECKS, DEFAULT_CHECK, get_check
from elenchus.core.config import CheckConfig, Config, load_config
from elenchus.core.engine import Engine, normalize
from elenchus.core.errors import friendly_error
from elenchus.core.prompt import build_system, normalize_sensitivity
from elenchus.core.providers import (
    MalformedOutput,
    ProviderError,
    ProviderRateLimited,
    ProviderRefusal,
    StubProvider,
    build_provider,
)
from elenchus.core.ratelimit import RateLimiter

CANNED = {
    "overall": "Attacks the person instead of the claim.",
    "findings": [
        {"name": "Ad hominem", "quote": "not even a scientist",
         "explanation": "Rejects the claim based on the person.", "confidence": "high"},
    ],
}
LONG = "You can't trust him, he's not even a scientist and he drives an SUV, so his study is worthless."


class FakeProvider:
    def __init__(self):
        self.last = None

    def analyze(self, system, user, schema, effort):
        self.last = {"system": system, "user": user, "schema": schema, "effort": effort}
        return dict(CANNED)


# ---- checks / techniques -------------------------------------------------

def test_registry_and_technique_counts():
    assert set(CHECKS) == {"fallacy", "rhetoric", "bias", "scam"} and DEFAULT_CHECK == "fallacy"
    assert len(get_check("fallacy").techniques) == 25
    assert len(get_check("rhetoric").techniques) == 14
    assert len(get_check("bias").techniques) == 10
    assert len(get_check("scam").techniques) == 8
    # every technique id is unique within its check
    for c in CHECKS.values():
        ids = [t.id for t in c.techniques]
        assert len(ids) == len(set(ids))


def test_rhetoric_routes_to_its_prompt():
    fake = FakeProvider()
    Engine(Config(provider="stub"), provider=fake).analyze(
        "Just asking questions here, but why won't anyone address the real issue?", check="rhetoric"
    )
    assert "bad-faith" in fake.last["system"].lower() and "Gish gallop:" in fake.last["system"]


def test_get_check_falls_back_to_default():
    assert get_check("nope").id == DEFAULT_CHECK and get_check("scam").id == "scam"


def test_build_system_is_strict_allowlist():
    techs = get_check("fallacy").techniques[:2]  # Ad hominem, Strawman
    s = build_system("FRAMING", "logical fallacies", techs, "lenient")
    assert "Report ONLY" in s and "Ad hominem:" in s and "Strawman:" in s
    assert "LENIENT" in s and "Slippery slope" not in s  # only the 2 passed in


# ---- engine --------------------------------------------------------------

def test_engine_routes_and_normalizes():
    fake = FakeProvider()
    eng = Engine(Config(provider="stub"), provider=fake)
    r = eng.analyze(LONG)
    assert r["findings"][0]["name"] == "Ad hominem"
    assert "logical-fallacy detector" in fake.last["system"].lower()


def test_engine_scam_routes_to_scam_prompt():
    fake = FakeProvider()
    Engine(Config(provider="stub"), provider=fake).analyze(
        "URGENT: send your password and a gift card to restore access now.", check="scam"
    )
    assert "scam and manipulation detector" in fake.last["system"].lower()


def test_engine_technique_toggle_removes_from_prompt():
    fake = FakeProvider()
    cfg = Config(provider="stub", checks={"fallacy": CheckConfig(techniques={"ad_hominem": False})})
    Engine(cfg, provider=fake).analyze(LONG)
    assert "Strawman:" in fake.last["system"]
    assert "Ad hominem:" not in fake.last["system"]  # toggled off -> absent from allow-list


def test_engine_no_techniques_skips_provider():
    fake = FakeProvider()
    all_off = {t.id: False for t in get_check("fallacy").techniques}
    cfg = Config(provider="stub", checks={"fallacy": CheckConfig(techniques=all_off)})
    r = Engine(cfg, provider=fake).analyze(LONG)
    assert r["findings"] == [] and "No techniques" in r["overall"]
    assert fake.last is None  # provider never called


def test_engine_disabled_check_returns_message():
    fake = FakeProvider()
    cfg = Config(provider="stub", checks={"scam": CheckConfig(enabled=False)})
    r = Engine(cfg, provider=fake).analyze("A long enough message to pass the length guard here.", check="scam")
    assert r["findings"] == [] and "disabled" in r["overall"].lower()
    assert fake.last is None  # provider never called for a disabled check


def test_engine_short_input_skips_provider():
    fake = FakeProvider()
    r = Engine(Config(provider="stub"), provider=fake).analyze("no")
    assert r["findings"] == [] and "short" in r["overall"].lower()
    assert fake.last is None


def test_engine_sensitivity_override():
    fake = FakeProvider()
    Engine(Config(provider="stub"), provider=fake).analyze(LONG, sensitivity="strict")
    assert "STRICT" in fake.last["system"]


# ---- config --------------------------------------------------------------

def test_config_file_parsing_and_env_override():
    yaml_text = (
        "provider: openai\nmodel: gpt-4o\nsensitivity: strict\nrate_limit_per_min: 9\n"
        "checks:\n  scam:\n    enabled: false\n  fallacy:\n    techniques:\n      bandwagon: false\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(yaml_text)
        path = f.name
    # Isolate from any ambient .env so file values show through as intended.
    keys = ["LLM_PROVIDER", "LLM_MODEL", "LLM_EFFORT", "FALLACY_SENSITIVITY", "RATE_LIMIT_PER_MIN"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        c = load_config(path)
        assert c.provider == "openai" and c.model == "gpt-4o" and c.sensitivity == "strict"
        assert c.rate_limit_per_min == 9
        assert c.check_enabled("scam") is False and c.check_enabled("fallacy") is True
        assert c.checks["fallacy"].techniques["bandwagon"] is False
        assert c.enabled_techniques(get_check("fallacy"))  # still has techniques left
        os.environ["LLM_PROVIDER"] = "stub"
        assert load_config(path).provider == "stub"  # env wins over file
    finally:
        os.environ.pop("LLM_PROVIDER", None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
        os.unlink(path)


def test_config_default_models_per_provider():
    from elenchus.core.config import _DEFAULT_MODELS

    assert _DEFAULT_MODELS["google"].startswith("gemini")
    assert _DEFAULT_MODELS["anthropic"].startswith("claude")


# ---- providers -----------------------------------------------------------

def test_normalize_coerces_and_skips_junk():
    r = normalize({
        "overall": 123,
        "findings": [
            {"name": "A", "quote": "q", "explanation": "e", "confidence": "weird"},
            "not a dict",
            {"name": "", "explanation": ""},
            {"name": "B", "explanation": "e2"},
        ],
    })
    assert r["overall"] == "" and [f["name"] for f in r["findings"]] == ["A", "B"]
    assert r["findings"][0]["confidence"] == "medium" and r["findings"][1]["quote"] == ""


def test_normalize_rejects_non_dict():
    try:
        normalize(["nope"])
    except MalformedOutput:
        return
    raise AssertionError("non-dict output was not rejected")


def test_friendly_error_maps_types():
    assert "declined" in friendly_error(ProviderRefusal()).lower()
    assert "busy or out of quota" in friendly_error(ProviderRateLimited()).lower()
    assert "couldn't read" in friendly_error(MalformedOutput()).lower()
    assert "went wrong" in friendly_error(ValueError("boom")).lower()


def test_normalize_sensitivity():
    assert normalize_sensitivity("STRICT") == "strict"
    assert normalize_sensitivity("bogus") == "balanced"
    assert normalize_sensitivity(None) == "balanced"


def test_stub_provider_returns_findings():
    body = "a message well over forty characters long that should produce a stub finding here"
    r = StubProvider().analyze("sys", f"<message>\n{body}\n</message>", {}, "medium")
    assert r["findings"] and r["findings"][0]["confidence"] in {"low", "medium", "high"}


def test_stub_provider_error_triggers():
    stub = StubProvider()
    for trig, exc in [("__refuse__", ProviderRefusal), ("__ratelimit__", ProviderRateLimited), ("__error__", ProviderError)]:
        try:
            stub.analyze("s", f"<message>\n{trig}\n</message>", {}, "medium")
        except exc:
            continue
        raise AssertionError(f"{trig} did not raise {exc.__name__}")


def test_build_provider_stub_and_google():
    assert type(build_provider(Config(provider="stub", model="stub"))).__name__ == "StubProvider"
    try:
        p = build_provider(Config(provider="google", model="gemini-2.0-flash", google_api_key="gkey"))
    except ImportError:
        return  # openai SDK not installed
    assert type(p).__name__ == "OpenAIProvider"
    assert "generativelanguage.googleapis.com" in str(p.client.base_url)


def test_unknown_provider_rejected():
    try:
        build_provider(Config(provider="bogus"))
    except ValueError:
        return
    raise AssertionError("bad provider was not rejected")


# ---- rate limiter --------------------------------------------------------

def test_ratelimiter_blocks_after_max():
    rl = RateLimiter(2, window_seconds=60)
    assert rl.check("u")[0] and rl.check("u")[0]
    ok, retry = rl.check("u")
    assert ok is False and retry > 0
    assert rl.check("other")[0]  # separate key independent


def test_ratelimiter_disabled_when_zero():
    rl = RateLimiter(0)
    assert rl.enabled is False and all(rl.check("u")[0] for _ in range(10))


def test_config_rate_limit_zero_disables():
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("rate_limit_per_min: 0\n")
        path = f.name
    saved = os.environ.pop("RATE_LIMIT_PER_MIN", None)
    try:
        assert load_config(path).rate_limit_per_min == 0  # 0 must survive (disables), not become 5
    finally:
        if saved is not None:
            os.environ["RATE_LIMIT_PER_MIN"] = saved
        os.unlink(path)


def test_openai_degradation_ladder_retries_without_reasoning_effort():
    from elenchus.core.providers import OpenAIProvider

    calls = []

    class FakeResp:
        def __init__(self, content):
            self.choices = [type("C", (), {"message": type("M", (), {"content": content})})]

    class FakeCompletions:
        def create(self, **kw):
            calls.append(kw)
            if "reasoning_effort" in kw:  # backend rejects it -> ladder must drop it and retry
                raise ValueError("unsupported parameter: reasoning_effort")
            return FakeResp('{"overall": "ok", "findings": []}')

    p = OpenAIProvider.__new__(OpenAIProvider)  # skip __init__ (which imports the openai SDK)
    p.model = "gpt-4o-mini"
    p.client = type("Client", (), {"chat": type("Chat", (), {"completions": FakeCompletions()})})
    out = p.analyze("sys", "<message>\nhi\n</message>", {"type": "object"}, "medium")
    assert out == {"overall": "ok", "findings": []}
    assert len(calls) == 2 and "reasoning_effort" in calls[0] and "reasoning_effort" not in calls[1]


if __name__ == "__main__":
    tests = sorted((k, v) for k, v in globals().items() if k.startswith("test_") and callable(v))
    for name, fn in tests:
        fn()
        print("PASS", name)
    print(f"\nOK — {len(tests)} tests passed")
