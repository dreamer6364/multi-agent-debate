"""连带探测：配额 429 时自建辩题是否残留（孤儿 topic）；重复重试是否堆积。"""
import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from httpx import ASGITransport, AsyncClient  # noqa: E402

from tests.api.conftest import make_app  # noqa: E402


async def main() -> None:
    app = make_app(BACKEND_ROOT, None)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/auth/register", json={"username": "alice", "password": "password8"})
            await c.post("/api/auth/login", json={"username": "alice", "password": "password8"})
            ct = {"title": "测试辩题A / 辩题B", "pro_points": "p1", "con_points": "c1",
                  "pro_stance": "正", "con_stance": "反"}
            # 第一场：自建辩题，创建成功
            r1 = await c.post("/api/debates", json={"custom_topic": ct, "format": "lite"})
            print("first:", r1.status_code)
            # 第二场：同样自建辩题（不同 title 避免干扰）——配额满应 429
            ct2 = dict(ct, title="测试辩题C / 辩题D")
            r2 = await c.post("/api/debates", json={"custom_topic": ct2, "format": "lite"})
            print("second:", r2.status_code, r2.text[:100])
            # 查辩题列表：429 的那条辩题是否已入库？
            topics = (await c.get("/api/topics")).json()
            titles = [t["title"] for t in (topics if isinstance(topics, list) else topics.get("items", []))]
            print("topics:", titles)
            orphan = "测试辩题C / 辩题D" in titles
            print("ORPHAN_TOPIC_ON_429:", orphan)
            # 再重试 2 次，看是否继续堆积
            for i in range(2):
                await c.post("/api/debates", json={"custom_topic": ct2, "format": "lite"})
            topics2 = (await c.get("/api/topics")).json()
            titles2 = [t["title"] for t in (topics2 if isinstance(topics2, list) else topics2.get("items", []))]
            print("topics after retries:", titles2.count("测试辩题C / 辩题D"))
            # 停第一场
            if r1.status_code == 202:
                await c.post(f"/api/debates/{r1.json()['id']}/stop")


asyncio.run(main())
