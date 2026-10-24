"""一次性数据迁移：SQLite（debate-dev.db）→ MySQL。

用途：全栈切换 MySQL 时保留历史账号、辩题与对局（bcrypt 哈希可移植，原密码可直接登录）。
用法：python scripts/migrate_sqlite_to_mysql.py [--sqlite PATH] [--dry-run]
表序遵循外键依赖：users → topics → debates → speeches → scorecards；ID 原样保留。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, insert, select
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from debate.db.models import (  # noqa: E402
    DebateRow,
    ScorecardRow,
    SpeechRow,
    TopicRow,
    User,
)

TABLES_IN_ORDER = [User, TopicRow, DebateRow, SpeechRow, ScorecardRow]


def load_sqlite_rows(sqlite_path: Path, table: Table) -> list[dict]:
    engine = create_engine(f"sqlite:///{sqlite_path}")
    metadata = MetaData()
    reflect = Table(table.__tablename__, metadata, autoload_with=engine)
    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(select(reflect))]
    engine.dispose()
    return rows


async def push_to_mysql(mysql_url: str, table: Table, rows: list[dict]) -> int:
    engine = create_async_engine(mysql_url)
    metadata = MetaData()

    def _load_target(sync_conn):
        return Table(table.__tablename__, metadata, autoload_with=sync_conn)

    async with engine.begin() as conn:
        # 显式按 MySQL 端表定义建表对象（异步连接需经 run_sync 反射）
        target = await conn.run_sync(_load_target)
        if rows:
            await conn.execute(target.delete())  # 幂等：目标表该表清空后整表写入
            await conn.execute(insert(target), rows)
    await engine.dispose()
    return len(rows)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default=str(BACKEND_ROOT / "debate-dev.db"))
    parser.add_argument("--mysql", default=os.environ.get("MYSQL_URL", ""))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.mysql:
        print("缺少 --mysql 或 MYSQL_URL")
        sys.exit(1)

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        print(f"SQLite 源不存在: {sqlite_path}")
        sys.exit(1)

    for model in TABLES_IN_ORDER:
        rows = load_sqlite_rows(sqlite_path, model)
        print(f"{model.__tablename__:<12} 源 {len(rows)} 行", end="")
        if not args.dry_run:
            n = await push_to_mysql(args.mysql, model, rows)
            print(f" → 写入 {n} 行")
        else:
            print("（dry-run 跳过写入）")


if __name__ == "__main__":
    import os

    asyncio.run(main())
