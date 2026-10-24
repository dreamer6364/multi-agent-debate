"""全局 fixture（TEST_PLAN §2.2）：资产根目录与配置。"""
from __future__ import annotations

from pathlib import Path

import pytest

from debate.core.config import AppConfig, load_config

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def backend_root() -> Path:
    return BACKEND_ROOT


@pytest.fixture(scope="session")
def config(backend_root: Path) -> AppConfig:
    return load_config(backend_root / "config.yaml")


@pytest.fixture(scope="session")
def assets_root(backend_root: Path) -> Path:
    return backend_root
