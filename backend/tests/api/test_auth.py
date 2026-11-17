"""认证集成测试（TEST_PLAN IT-AUT-01~05，SPEC §5.2）。"""

import asyncio

from .conftest import new_client, register_and_login


async def test_register_success_and_duplicate(env):
    client = await new_client(env)
    response = await client.post("/api/auth/register", json={"username": "alice", "password": "password8"})
    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "alice" and body["id"] > 0

    response = await client.post("/api/auth/register", json={"username": "alice", "password": "password8"})
    assert response.status_code == 409 and response.json()["code"] == "USERNAME_TAKEN"
    await client.aclose()


async def test_register_validation(env):
    client = await new_client(env)
    short = await client.post("/api/auth/register", json={"username": "bob", "password": "short"})
    assert short.status_code == 422 and short.json()["code"] == "VALIDATION_ERROR"
    bad_name = await client.post("/api/auth/register", json={"username": "非法名称!", "password": "password8"})
    assert bad_name.status_code == 422
    await client.aclose()


async def test_login_sets_httponly_cookie(env):
    client = await new_client(env)
    await client.post("/api/auth/register", json={"username": "alice", "password": "password8"})
    response = await client.post("/api/auth/login", json={"username": "alice", "password": "password8"})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "access_token=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie

    wrong = await client.post("/api/auth/login", json={"username": "alice", "password": "wrong-pass"})
    assert wrong.status_code == 401 and wrong.json()["code"] == "AUTH_INVALID"
    await client.aclose()


async def test_me_requires_auth(env):
    client = await new_client(env)
    response = await client.get("/api/auth/me")
    assert response.status_code == 401 and response.json()["code"] == "AUTH_REQUIRED"
    await client.aclose()


async def test_me_returns_quota(env):
    client = await new_client(env)
    await register_and_login(client, "alice")
    response = await client.get("/api/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alice"
    assert body["quota"] == {"concurrent_limit": 1, "daily_limit": None}  # v1.3：无每日配额
    await client.aclose()


# ---- ISS-014 C-1：登录失败限流（每 AppState 一份 guard → 测试间天然隔离）----

async def test_login_lockout_after_repeated_failures(env):
    client = await new_client(env)
    await client.post("/api/auth/register", json={"username": "brute", "password": "password8"})
    guard = env.app.state.svc.login_guard
    guard.base_delay = 0.05  # 测试不等默认 30s

    for _ in range(5):
        failed = await client.post("/api/auth/login", json={"username": "brute", "password": "wrong-pass"})
        assert failed.status_code == 401 and failed.json()["code"] == "AUTH_INVALID"

    locked = await client.post("/api/auth/login", json={"username": "brute", "password": "password8"})
    assert locked.status_code == 429 and locked.json()["code"] == "AUTH_RATE_LIMITED"

    await asyncio.sleep(0.1)  # 锁到期后正确密码放行
    ok = await client.post("/api/auth/login", json={"username": "brute", "password": "password8"})
    assert ok.status_code == 200
    await client.aclose()


async def test_lockout_scoped_per_user_and_cleared_on_success(env):
    client = await new_client(env)
    for username in ("brute", "alice"):
        await client.post("/api/auth/register", json={"username": username, "password": "password8"})
    guard = env.app.state.svc.login_guard
    guard.base_delay = 0.05

    for _ in range(5):
        await client.post("/api/auth/login", json={"username": "brute", "password": "wrong-pass"})
    other = await client.post("/api/auth/login", json={"username": "alice", "password": "password8"})
    assert other.status_code == 200  # 锁定只针对失败账号，不株连其他用户
    assert other.headers.get("set-cookie")

    await asyncio.sleep(0.1)
    assert (await client.post("/api/auth/login",
                              json={"username": "brute", "password": "password8"})).status_code == 200
    for _ in range(4):  # 成功后计数清零：再失败 4 次仍只是 401，不再触发锁定
        failed = await client.post("/api/auth/login", json={"username": "brute", "password": "wrong-pass"})
        assert failed.status_code == 401
    await client.aclose()
