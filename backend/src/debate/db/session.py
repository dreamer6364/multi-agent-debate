"""异步引擎与会话工厂。生产走 MYSQL_URL（MySQL 8 / asyncmy）；
未配置时回落 SQLite 开发库，测试注入 db_url（aiosqlite）。
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel


def make_engine(db_url: str | None = None, *, base_dir: Path | None = None) -> AsyncEngine:
    url = db_url or os.environ.get("MYSQL_URL")
    if not url:
        dev_db = (base_dir or Path.cwd()) / "debate-dev.db"
        url = f"sqlite+aiosqlite:///{dev_db}"
    return create_async_engine(url, echo=False)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """测试/开发引导建表；生产首次部署用 Alembic（CONSTRAINTS V4），见 docs 备注。"""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
