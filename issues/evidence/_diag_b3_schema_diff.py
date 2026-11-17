"""B-3 诊断：alembic head schema vs SQLModel.metadata(create_all) schema 是否一致。

- 库 A：空 SQLite 跑 `alembic upgrade head`（迁移链产出）
- 库 B：空 SQLite 跑 `SQLModel.metadata.create_all`（启动引导产出）
- 用 inspector 对比两库的表/列/索引集合，输出差异。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from debate.db import models  # noqa: E402,F401  注册 metadata
from sqlmodel import SQLModel  # noqa: E402


def schema_of(db_path: Path) -> dict:
    eng = create_engine(f"sqlite:///{db_path}")
    insp = inspect(eng)
    out = {}
    for t in sorted(insp.get_table_names()):
        cols = {c["name"]: str(c["type"]) for c in insp.get_columns(t)}
        idx = sorted(i["name"] for i in insp.get_indexes(t))
        out[t] = {"cols": cols, "idx": idx}
    eng.dispose()
    return out


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="b3_diag_"))
    db_mig = tmp / "mig.db"
    db_crt = tmp / "crt.db"

    # 1) 迁移链
    env = {**os.environ, "MYSQL_URL": f"sqlite+aiosqlite:///{db_mig}"}
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(BACKEND / "alembic.ini"), "upgrade", "head"],
        cwd=BACKEND, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print("== alembic upgrade head ==")
    print(r.stdout[-2000:] if r.stdout else "", r.stderr[-2000:] if r.stderr else "")
    if r.returncode != 0:
        print("ALEMBIC_FAILED rc=", r.returncode)
        return

    # 2) create_all
    eng = create_engine(f"sqlite:///{db_crt}")
    SQLModel.metadata.create_all(eng)
    eng.dispose()

    a, b = schema_of(db_mig), schema_of(db_crt)
    print("\n== 表集合 ==")
    print("迁移 only:", sorted(set(a) - set(b)))
    print("create_all only:", sorted(set(b) - set(a)))

    print("\n== 列差异（迁移 vs create_all）==")
    diff = False
    for t in sorted(set(a) & set(b)):
        ca, cb = a[t]["cols"], b[t]["cols"]
        for col in sorted(set(ca) - set(cb)):
            print(f"  [迁移缺列] {t}.{col} (create_all 有)")
            diff = True
        for col in sorted(set(cb) - set(ca)):
            print(f"  [迁移多列] {t}.{col} (迁移有)")
            diff = True
        for col in sorted(set(ca) & set(cb)):
            if ca[col].lower() != cb[col].lower():
                print(f"  [类型差异] {t}.{col}: 迁移={ca[col]} create_all={cb[col]}")
                diff = True
        if a[t]["idx"] != b[t]["idx"]:
            print(f"  [索引差异] {t}: 迁移={a[t]['idx']} create_all={b[t]['idx']}")
            diff = True
    print("\nRESULT:", "SCHEMA_MISMATCH" if diff else "SCHEMA_IDENTICAL")


if __name__ == "__main__":
    main()
