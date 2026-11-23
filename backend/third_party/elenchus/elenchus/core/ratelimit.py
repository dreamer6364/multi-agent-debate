"""A small in-process per-user rate limiter (sliding window).

Keeps one user from burning API quota in a loop. In-memory only — fine for a
single-process bot; swap for Redis if you ever run multiple instances. The Engine
owns one of these (built from the config's rate_limit_per_min); frontends call it.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

WINDOW_SECONDS = 60


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int = WINDOW_SECONDS):
        self.max = max_requests
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.max > 0

    def check(self, key: str) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds). retry_after is 0 when allowed."""
        if not self.enabled:
            return True, 0.0
        now = time.monotonic()
        with self._lock:
            dq = self._hits[key]
            while dq and now - dq[0] > self.window:
                dq.popleft()
            if len(dq) >= self.max:
                return False, max(0.0, self.window - (now - dq[0]))
            dq.append(now)
            return True, 0.0
