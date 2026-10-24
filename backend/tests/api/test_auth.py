"""认证集成测试（TEST_PLAN IT-AUT-01~05，SPEC §5.2）。"""
import pytest

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
    assert body["quota"] == {"daily_used": 0, "daily_limit": 5}
    await client.aclose()
