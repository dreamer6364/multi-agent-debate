"""ISS-014 C-1 登录失败限流单测（进程内失败计数 + 指数退避，SPEC §5.1/§5.2）。"""
from __future__ import annotations

import pytest

from debate.api.errors import ApiError
from debate.api.login_guard import LoginGuard


class Clock:
    """可快进的单调时钟（避免测试真等 30s）。"""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_failed_attempts_lock_then_exponential_backoff():
    clock = Clock()
    guard = LoginGuard(clock=clock, threshold=3, base_delay=10.0, max_delay=40.0)
    for _ in range(2):
        guard.record_failure("Alice")
        guard.check("Alice")  # 未到阈值：不拦
    guard.record_failure("Alice")
    with pytest.raises(ApiError) as exc_info:
        guard.check(" alice ")  # 归一化：大小写/首尾空格视为同一账号
    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "AUTH_RATE_LIMITED"
    assert "10 秒" in exc_info.value.message

    clock.t += 5
    with pytest.raises(ApiError):  # 锁定期内继续拒绝，不升级计数
        guard.check("alice")
    clock.t += 5
    guard.check("alice")  # 到期解锁
    guard.record_failure("alice")  # 第 4 次失败 → 退避翻倍
    with pytest.raises(ApiError) as escalated:
        guard.check("alice")
    assert "20 秒" in escalated.value.message


def test_success_clears_counter():
    clock = Clock()
    guard = LoginGuard(clock=clock, threshold=3, base_delay=10.0)
    guard.record_failure("bob")
    guard.record_failure("bob")
    guard.record_success("bob")
    guard.record_failure("bob")
    guard.record_failure("bob")
    guard.check("bob")  # 清零后重新计数，未到阈值不锁


def test_lock_cap_and_key_eviction():
    clock = Clock()
    guard = LoginGuard(clock=clock, threshold=1, base_delay=10.0, max_delay=25.0, max_keys=3)
    guard.record_failure("a")
    for expected in ("10 秒", "20 秒", "25 秒"):  # 第三次应封顶 25s（40 → 25）
        with pytest.raises(ApiError) as exc_info:
            guard.check("a")
        assert expected in exc_info.value.message
        clock.t += 26  # 快进过锁
        guard.check("a")
        guard.record_failure("a")

    for name in ("b", "c", "d", "e"):
        guard.record_failure(name)
    assert len(guard._entries) == 3  # 随机用户名刷不爆内存（淘汰最久未活动）


def test_failures_are_scoped_per_username():
    guard = LoginGuard(clock=Clock(), threshold=2, base_delay=10.0)
    guard.record_failure("alice")
    guard.check("bob")  # 别的账号不受牵连
    guard.check("Alice")  # 同账号不同写法仍共享计数（归一化）
