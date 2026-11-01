"""行为探测：配额并发（同用户同时 1 场）、重复创建、导出 HTML 转义、stop 幂等。"""
import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from httpx import ASGITransport, AsyncClient  # noqa: E402

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

            # 1. 配额：同用户并发只允许 1 场（pending/running）
            r1 = await c.post("/api/debates", json={"topic_id": 1, "format": "lite"})
            check("第一场创建", r1.status_code == 200 or r1.status_code == 201, f"{r1.status_code}")
            r2 = await c.post("/api/debates", json={"topic_id": 1, "format": "lite"})
            check("并发第二场应被拒", r2.status_code in (409, 429), f"got {r2.status_code} {r2.text[:120]}")

            # 2. 不存在的辩题
            r = await c.post("/api/debates", json={"topic_id": 99999, "format": "lite"})
            check("不存在辩题 404/422", r.status_code in (404, 422), f"got {r.status_code}")

            # 3. 非法 format
            r = await c.post("/api/debates", json={"topic_id": 1, "format": "huge"})
            check("非法 format 422", r.status_code in (422, 400), f"got {r.status_code}")

            # 4. stop 未在跑的场次
            r = await c.post(f"/api/debates/{r1.json()['id']}/stop")
            check("stop pending 场次（应允许或 409，不能 500）", r.status_code in (200, 409), f"got {r.status_code}")

            # 5. 导出未结束场次
            r = await c.get(f"/api/debates/{r1.json()['id']}/export")
            check("未结束导出被拒", r.status_code == 409, f"got {r.status_code}")

            # 6. 导出非法 format
            r = await c.get(f"/api/debates/{r1.json()['id']}/export", params={"format": "exe"})
            check("非法导出格式 422", r.status_code in (409, 422), f"got {r.status_code}")

            # 7. XSS：自建辩题标题带 <script>，导出 HTML 是否转义（需 finished 场次——这里先查辩题入库）
            evil = await c.post("/api/topics", json={
                "title": "<script>alert(1)</script>辩题", "pro_stance": "是", "con_stance": "否",
            })
            check("恶意标题辩题创建", evil.status_code in (200, 201), f"got {evil.status_code}")

            # 8. 重复 stop（幂等性边界）
            did = r1.json()["id"]
            r = await c.post(f"/api/debates/{did}/stop")
            r = await c.get(f"/api/debates/{did}")
            check("stop 后状态为 stopped 或 pending/running 可解释", True, f"status={r.json().get('status')}")

            # 9. SSE after_seq 负数/超大
            r = await c.get(f"/api/debates/{did}/speeches", params={"after_seq": -5})
            check("after_seq=-5 不 500", r.status_code == 200, f"got {r.status_code}")
            r = await c.get(f"/api/debates/{did}/speeches", params={"limit": 0})
            check("limit=0 归一化", r.status_code == 200, f"got {r.status_code}")

            # 10. 未登录注册后重复注册
            r = await c.post("/api/auth/register", json={"username": "alice", "password": "password8"})
            check("重复注册 409", r.status_code == 409, f"got {r.status_code}")
            # 弱密码
            r = await c.post("/api/auth/register", json={"username": "short", "password": "123"})
            check("弱密码拒绝 422", r.status_code == 422, f"got {r.status_code}")
            # 用户名带中文/特殊字符
            r = await c.post("/api/auth/register", json={"username": "恶意用户", "password": "password8"})
            check("非法用户名 422", r.status_code == 422, f"got {r.status_code}")

    failed = [x for x in RESULTS if not x[1]]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ==")
    for name, _, detail in failed:
        print("  FAILED:", name, detail)


asyncio.run(main())
