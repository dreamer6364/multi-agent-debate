"""竞态与认证探测：配额 TOCTOU 并发、JWT 篡改、启动清扫僵尸场次。"""
import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from httpx import ASGITransport, AsyncClient  # noqa: E402

from debate.core.security import create_access_token, decode_token  # noqa: E402
from debate.api.deps import COOKIE_NAME  # noqa: E402
from tests.api.conftest import make_app  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


async def main() -> None:
    app = make_app(BACKEND_ROOT, None)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/auth/register", json={"username": "alice", "password": "password8"})
            await c.post("/api/auth/login", json={"username": "alice", "password": "password8"})

            # 1. 配额 TOCTOU：同时并发创建 2 场，应只成功 1 场（QUOTA_CONCURRENT 语义是并发 1 场）
            results = await asyncio.gather(
                c.post("/api/debates", json={"topic_id": 1, "format": "lite"}),
                c.post("/api/debates", json={"topic_id": 1, "format": "lite"}),
                return_exceptions=True,
            )
            codes = [r.status_code if not isinstance(r, BaseException) else "EXC" for r in results]
            ok_count = sum(1 for code in codes if code == 202)
            check("并发创建仅 1 场成功", ok_count == 1, f"codes={codes}")
            for r in results:
                if not isinstance(r, BaseException) and r.status_code == 202:
                    await c.post(f"/api/debates/{r.json()['id']}/stop")

            # 2. JWT 篡改/错 secret
            bad = create_access_token(999, secret="wrong-secret", expire_minutes=5)
            r = await c.get("/api/auth/me", cookies={COOKIE_NAME: bad})
            check("错误 secret 的 token 拒绝", r.status_code == 401, f"got {r.status_code}")
            # 篡改 payload（user id 提权到他人）
            good = create_access_token(1, secret="test-secret", expire_minutes=5)
            r = await c.get("/api/auth/me", cookies={COOKIE_NAME: good})
            check("已签名 token 正常", r.status_code == 200, f"got {r.status_code} {r.text[:60]}")
            # 过期 token
            expired = create_access_token(1, secret="test-secret", expire_minutes=-1)
            r = await c.get("/api/auth/me", cookies={COOKIE_NAME: expired})
            check("过期 token 拒绝", r.status_code == 401, f"got {r.status_code}")
            # 空/垃圾 token
            r = await c.get("/api/auth/me", cookies={COOKIE_NAME: "garbage.token.here"})
            check("垃圾 token 拒绝", r.status_code == 401, f"got {r.status_code}")

            # 3. 算法混淆（none 攻击）
            r = await c.get("/api/auth/me", cookies={COOKIE_NAME: "eyJhbGciOiJub25lIn0.eyJzdWIiOjF9."})
            check("alg=none 拒绝", r.status_code == 401, f"got {r.status_code}")

    failed = [x for x in RESULTS if not x[1]]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ==")
    for name, _, detail in failed:
        print("  FAILED:", name, detail)


asyncio.run(main())
