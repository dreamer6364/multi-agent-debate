"""登录失败限流（ISS-014 C-1，SPEC §5.1/§5.2）：进程内按用户名的失败计数 + 指数退避。

- 每 ``AppState`` 一份实例：生产单实例语义（多 worker 不共享，口径见 ISS-014 C-1）；
  API 测试每应用新建 state → 天然隔离，不串号；
- 连续失败 threshold（默认 5）次后锁定 base_delay（默认 30s），此后每多失败一次
  锁定时间翻倍（上限 max_delay，默认 900s）；成功登录立即清零；
- 锁定期内直接 429（``AUTH_RATE_LIMITED``），不查库、不跑 bcrypt——省 CPU 且
  避免单账号失败风暴放大成本；锁定期满保留失败计数，继续失败会升级退避；
- 键数量上限 + 淘汰最久未活动条目，防随机用户名刷爆内存。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable

from .errors import ApiError


@dataclass
class _Entry:
    failures: int = 0
    locked_until: float = 0.0
    updated: float = 0.0


class LoginGuard:
    """单实例进程内限流器；时钟可注入以便单测快进。"""

    def __init__(
        self,
        *,
        threshold: int = 5,
        base_delay: float = 30.0,
        max_delay: float = 900.0,
        max_keys: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_keys = max_keys
        self._clock = clock
        self._entries: dict[str, _Entry] = {}

    @staticmethod
    def _key(username: str) -> str:
        return username.strip().lower()

    def check(self, username: str) -> None:
        """登录前检查：锁定中抛 429（不查库不校验密码）；到期自动解锁但保留失败数。"""
        entry = self._entries.get(self._key(username))
        if entry is None:
            return
        now = self._clock()
        if entry.locked_until > now:
            seconds = max(1, math.ceil(entry.locked_until - now))
            raise ApiError(429, "AUTH_RATE_LIMITED", f"登录尝试次数过多，请 {seconds} 秒后重试")
        if entry.locked_until:  # 锁已到期：保留 failures，后续失败继续升级退避
            entry.locked_until = 0.0

    def record_failure(self, username: str) -> None:
        key = self._key(username)
        now = self._clock()
        entry = self._entries.get(key)
        if entry is None:
            entry = self._entries[key] = _Entry()
        entry.failures += 1
        entry.updated = now
        if entry.failures >= self.threshold:
            exponent = min(entry.failures - self.threshold, 20)  # 防大指数大整数
            entry.locked_until = now + min(self.max_delay, self.base_delay * 2**exponent)
        self._evict()

    def record_success(self, username: str) -> None:
        self._entries.pop(self._key(username), None)

    def _evict(self) -> None:
        if len(self._entries) <= self.max_keys:
            return
        excess = len(self._entries) - self.max_keys
        for key in sorted(self._entries, key=lambda k: self._entries[k].updated)[:excess]:
            del self._entries[key]
