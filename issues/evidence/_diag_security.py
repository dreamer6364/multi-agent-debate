"""安全测试：跨用户越权（IDOR）与未授权访问探测。"""
import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from httpx import ASGITransport, AsyncClient  # noqa: E402

from debate.api.deps import COOKIE_NAME  # noqa: E402
from tests.api.conftest import make_app  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


async def register(client: AsyncClient, name: str) -> None:
    r = await client.post("/api/auth/register", json={"username": name, "password": "password8"})
    if r.status_code not in (201, 409):
        print("register unexpected:", name, r.status_code, r.text[:200])


async def main() -> None:
    app = make_app(BACKEND_ROOT, None)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as alice, \
                   AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as bob, \
                   AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as anon:
            await register(alice, "alice")
            await register(bob, "bob")
            await alice.post("/api/auth/login", json={"username": "alice", "password": "password8"})
            await bob.post("/api/auth/login", json={"username": "bob", "password": "password8"})

            # alice 建一个场次（pending，不启动引擎）
            r = await alice.post("/api/debates", json={"topic_id": 1, "format": "lite"})
            did = r.json()["id"]

            # 1. 越权读
            r = await bob.get(f"/api/debates/{did}")
            check("IDOR GET /debates/{id}", r.status_code in (401, 403, 404),
                  f"bob→alice 场次 got {r.status_code}")
            r = await bob.get(f"/api/debates/{did}/speeches")
            check("IDOR GET speeches", r.status_code in (401, 403, 404), f"got {r.status_code}")
            r = await bob.get(f"/api/debates/{did}/export")
            check("IDOR GET export", r.status_code in (401, 403, 404), f"got {r.status_code}")
            # 2. 越权操作
            r = await bob.post(f"/api/debates/{did}/stop")
            check("IDOR POST stop", r.status_code in (401, 403, 404, 409), f"got {r.status_code}")
            r = await bob.delete(f"/api/debates/{did}")
            check("IDOR DELETE", r.status_code in (401, 403, 404, 409), f"got {r.status_code}")
            # 3. 越权 SSE
            try:
                async with alice.stream("GET", f"/api/debates/{did}/stream") as resp_ok:
                    check("owner stream 允许", resp_ok.status_code == 200, f"got {resp_ok.status_code}")
                async with bob.stream("GET", f"/api/debates/{did}/stream") as resp:
                    check("IDOR stream", resp.status_code in (401, 403, 404),
                          f"got {resp.status_code}")
            except Exception as exc:  # noqa: BLE001
                check("IDOR stream", False, f"exception {exc!r}")
            # 4. 匿名
            r = await anon.get(f"/api/debates/{did}")
            check("匿名 GET", r.status_code in (401, 403), f"got {r.status_code}")
            r = await anon.get("/api/debates")
            check("匿名列表", r.status_code in (401, 403), f"got {r.status_code}")
            r = await anon.get("/api/debates/1/stream")
            check("匿名 stream", r.status_code in (401, 403), f"got {r.status_code}")
            # 5. 越权改他人清单：bob 清空归档不应删掉 alice 的场次
            r = await alice.get("/api/debates")
            before = len(r.json()["items"]) if isinstance(r.json(), dict) else len(r.json())
            r = await bob.delete("/api/debates")
            check("bob 清空归档不碰 alice", r.status_code == 200 and r.json().get("deleted") == 0,
                  f"got {r.status_code} {r.text[:80]}")
            r = await alice.get("/api/debates")
            after = r.json()["items"] if isinstance(r.json(), dict) else r.json()
            check("alice 场次仍在", len(after) == before, f"{before}→{len(after)}")
            # 6. 辩题：bob 不应删内置；bob 不应改 alice 的私有辩题
            r = await bob.delete("/api/topics/1")
            check("删内置辩题被拒", r.status_code in (403, 404), f"got {r.status_code}")

    failed = [x for x in RESULTS if not x[1]]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ==")
    for name, _, detail in failed:
        print("  FAILED:", name, detail)


asyncio.run(main())
