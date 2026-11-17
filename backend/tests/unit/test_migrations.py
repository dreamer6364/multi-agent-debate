"""迁移链回归（ISS-014 B-3）：部署链 `alembic upgrade head` 的三条保证。

1. 空库 upgrade 后 schema 与 `SQLModel.metadata.create_all` 完全一致
   ——防“改模型忘写迁移”（create_all 有列而迁移没有，上线即缺列）；
2. 历史 create_all 库（老部署，表已存在且无 alembic_version）upgrade 幂等通过
   并落到 head——baseline/fallacies 迁移已按此口径幂等化；
3. 重复 upgrade 幂等（已是 head 时 no-op）。

alembic 必须在子进程跑（env.py 内部 asyncio.run，不能进测试的事件循环）；
用 SQLite 临时文件库，CI 无需额外 MySQL。子进程 PYTHONPATH 显式前置 src，
确保 alembic 读到的是当前源码而非 venv 里已安装的快照。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

from debate.db import models  # noqa: F401  注册 SQLModel metadata（create_all 对照用）

HEAD_TABLES_EXCLUDED = {"alembic_version"}


def _run_alembic(backend_root: Path, db_url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "MYSQL_URL": db_url}
    src = str(backend_root / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(backend_root / "alembic.ini"), *args],
        cwd=backend_root, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _schema(db_path: Path) -> dict[str, dict[str, str]]:
    """{表: {列: 类型}}（排除 alembic_version），索引另列。"""
    eng = create_engine(f"sqlite:///{db_path}")
    insp = inspect(eng)
    out: dict[str, dict[str, str]] = {}
    for t in sorted(insp.get_table_names()):
        if t in HEAD_TABLES_EXCLUDED:
            continue
        out[t] = {c["name"]: str(c["type"]) for c in insp.get_columns(t)}
    eng.dispose()
    return out


def _current_version(db_path: Path) -> str | None:
    eng = create_engine(f"sqlite:///{db_path}")
    insp = inspect(eng)
    if not insp.has_table("alembic_version"):
        eng.dispose()
        return None
    with eng.connect() as conn:
        row = conn.exec_driver_sql("SELECT version_num FROM alembic_version").first()
    eng.dispose()
    return row[0] if row else None


def _head(backend_root: Path) -> str:
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(backend_root / "alembic.ini"), "heads"],
        cwd=backend_root, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.split()[0]  # 形如 "cc151eebab8a (head)"


def test_upgrade_head_matches_metadata(backend_root: Path, tmp_path: Path):
    """空库 upgrade → schema 与 create_all 一致（迁移链完整性，防改模型忘写迁移）。"""
    db = tmp_path / "empty.db"
    url = f"sqlite+aiosqlite:///{db}"
    r = _run_alembic(backend_root, url, "upgrade", "head")
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"

    migrated = _schema(db)

    ref = tmp_path / "ref.db"
    eng = create_engine(f"sqlite:///{ref}")
    SQLModel.metadata.create_all(eng)
    eng.dispose()
    expected = _schema(ref)

    assert set(migrated) == set(expected), (
        f"表集合不一致——模型改了但没写迁移？迁移多={set(migrated) - set(expected)} "
        f"模型多={set(expected) - set(migrated)}"
    )
    for t in expected:
        assert migrated[t] == expected[t], (
            f"表 {t} 列不一致——模型改了但没写迁移？"
            f" 迁移={migrated[t]} 模型={expected[t]}"
        )
    assert _current_version(db) == _head(backend_root)


def test_upgrade_idempotent_on_legacy_create_all_db(backend_root: Path, tmp_path: Path):
    """历史库（create_all 建表、无 alembic_version）：upgrade 不炸“表已存在”，落到 head；再 upgrade 为 no-op。"""
    db = tmp_path / "legacy.db"
    url = f"sqlite+aiosqlite:///{db}"

    # 模拟老部署：启动期 create_all 建表，从未跑过 alembic
    eng = create_engine(f"sqlite:///{db}")
    SQLModel.metadata.create_all(eng)
    eng.dispose()
    assert _current_version(db) is None

    r = _run_alembic(backend_root, url, "upgrade", "head")
    assert r.returncode == 0, f"历史库 upgrade 失败：\nstdout={r.stdout}\nstderr={r.stderr}"
    assert _current_version(db) == _head(backend_root)

    # 已到 head：重复执行幂等
    r2 = _run_alembic(backend_root, url, "upgrade", "head")
    assert r2.returncode == 0, f"重复 upgrade 失败：\nstdout={r2.stdout}\nstderr={r2.stderr}"
