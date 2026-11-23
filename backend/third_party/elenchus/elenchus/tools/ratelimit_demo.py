"""Offline demo of per-user rate limiting — no API key needed (uses the stub).

    python -m tools.ratelimit_demo [limit_per_window] [num_requests]

Simulates one user firing N rapid requests through the same limiter + analyzer
path the adapters use, and shows which get through vs blocked.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("LLM_PROVIDER", "stub")  # run with no key

from elenchus.core.analyzer import analyze_text  # noqa: E402  (import after setting provider)
from elenchus.core.ratelimit import RateLimiter  # noqa: E402

SAMPLE = (
    "Everyone knows this is true, so it must be correct, and only an idiot funded by "
    "big corporations would ever disagree with something so obvious."
)


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    limiter = RateLimiter(limit, window_seconds=60)
    user = "demo-user"

    print(f"Rate limit: {limit} requests / 60s. Firing {n} rapid requests as '{user}':\n")
    for i in range(1, n + 1):
        ok, retry = limiter.check(user)
        if ok:
            result = analyze_text(SAMPLE)
            print(f"  #{i}: ALLOWED  -> {len(result['findings'])} finding(s): {result['overall']}")
        else:
            print(f"  #{i}: BLOCKED  -> retry in {retry:.0f}s")
    print("\nDone. (No API calls were made — LLM_PROVIDER=stub.)")


if __name__ == "__main__":
    main()
