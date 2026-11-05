# ISS-013（P1-测试基建）SSE 集成测试被系统代理劫持——长挂的 7 个 test_sse 失败根因

**状态**：已修复（2026-11-05，v2.9.4）
**位置**：`tests/api/conftest.py:210`、`tests/api/test_sse.py`（5 处 `AsyncClient(base_url=live.base_url)`）

## 根因

开发者环境存在全局代理变量（`http_proxy/https_proxy=http://127.0.0.1:50071`，`no_proxy` 为空），
而 httpx `AsyncClient` 默认 `trust_env=True`——所有指向 `127.0.0.1:<随机测试端口>` 的请求
都被代理转发到错误目标（实测 register 打到了 8000 端口的开发后端，返回用户 id=2；
login 与 openapi.json 返回 404）。

**症状**：`tests/api/test_sse.py` 7 个用例全部失败（login 404），自 v2.8 起在多轮会话中
被误判为"预先存在的环境相关失败"——二分验证 e9f4b05→02e77fa 全部失败，与代码无关，
是环境变量 + 测试客户端未禁用代理的组合。ASGITransport 用例不受影响（不经过网络栈）。

## 修复

所有直连 LiveServer 的 AsyncClient 显式传 `trust_env=False`（测试服务器在 loopback，
永远不该走代理）：

```python
AsyncClient(base_url=live.base_url, trust_env=False)
```

**验收**：`pytest tests/api/test_sse.py` 7 passed（此前 7 failed）。

## 教训

- 诊断"稳定失败"时先做环境二分（stash 回 HEAD 复跑）只回答了"是否代码引入"，
  没回答"哪个环境因素"——真正破案靠的是手动起服务器 + 打印响应体（用户 id 泄露了
  目标库不对）。
- 涉及本地网络栈的测试基建应默认 `trust_env=False`，不信任开发者 shell 的代理变量。