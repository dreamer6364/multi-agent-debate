"""LLM 网关单测：重试次数、耗尽异常、用量统计（TEST_PLAN FT-LLM 前置，SPEC §7）。
v2.6.2：文本型工具调用（伪 XML 混进正文）的解析、剥离与流式压制。
"""
import pytest

from debate.engine.llm.gateway import (
    ContentFiltered,
    Gateway,
    LLMExhausted,
    _StreamToolFilter,
    parse_text_tool_calls,
    strip_tool_artifacts,
)
from tests.fakes.llm import FakeLLM

MSG = [{"role": "user", "content": "{repeat:7}"}]


def test_gateway_wraps_fake_and_counts_usage():
    gateway = Gateway(backend=FakeLLM(mode="echo"), model_debater="d", model_official="o",
                      max_retries=2, sleep=lambda s: None)
    result = gateway.complete(MSG, tier="debater", temperature=0.5, max_tokens=64)
    assert result.text == "字" * 7
    assert gateway.usage["calls"] == 1


def test_gateway_retries_then_exhausts():
    fake = FakeLLM(mode="fault", error_kind="http_500")
    gateway = Gateway(backend=fake, model_debater="d", model_official="o",
                      max_retries=2, sleep=lambda s: None)
    with pytest.raises(LLMExhausted):
        gateway.complete(MSG, tier="debater", temperature=0.5, max_tokens=64)
    assert len(fake.calls) == 3  # 首次 1 + 重试 2（SPEC §7：重试 3 次耗尽则占位降级）


def test_gateway_recovers_after_transient_failure():
    class Flaky:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("瞬态故障")
            return "恢复"

    gateway = Gateway(backend=Flaky(), model_debater="d", model_official="o",
                      max_retries=3, sleep=lambda s: None)
    assert gateway.complete(MSG, tier="judge", temperature=0.3, max_tokens=64).text == "恢复"


def test_stream_accumulates_and_counts_usage():
    fake = FakeLLM(mode="echo")
    gateway = Gateway(backend=fake, model_debater="d", model_official="o",
                      max_retries=2, sleep=lambda s: None)
    chunks = list(gateway.stream([{"role": "user", "content": "{repeat:9}"}],
                                 tier="debater", temperature=0.5, max_tokens=64))
    assert "".join(chunks) == "字" * 9
    assert gateway.usage["calls"] == 1
    assert gateway.usage["completion_tokens"] == 9


def test_stream_notifies_attempt_and_resets_on_retry():
    class FlakyStream:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            return "不应走到这里"

        def stream(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("首次流失败")
            yield "恢"
            yield "复"

    backend = FlakyStream()
    gateway = Gateway(backend=backend, model_debater="d", model_official="o",
                      max_retries=2, sleep=lambda s: None)
    attempts: list[int] = []
    chunks = list(gateway.stream([{"role": "user", "content": "x"}], tier="debater",
                                 temperature=0.5, max_tokens=64,
                                 on_attempt=attempts.append))
    assert "".join(chunks) == "恢复"
    assert attempts == [0, 1]  # 重试时 attempt 递增（前端据此重置累积）


def test_stream_exhausts_after_retries():
    gateway = Gateway(backend=FakeLLM(mode="fault", error_kind="http_500"),
                      model_debater="d", model_official="o",
                      max_retries=2, sleep=lambda s: None)
    with pytest.raises(LLMExhausted):
        list(gateway.stream([{"role": "user", "content": "x"}],
                            tier="debater", temperature=0.5, max_tokens=64))


class FakeRateLimitError(Exception):
    """模拟 openai.RateLimitError（按类名匹配，不依赖 openai 导入）。"""


def test_gateway_backs_off_long_on_rate_limit():
    class Throttled:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            if self.calls <= 2:  # 前两次限流，第三次恢复
                raise FakeRateLimitError("429 Too Many Requests")
            return "恢复"

    waits: list[float] = []
    gateway = Gateway(backend=Throttled(), model_debater="d", model_official="o",
                      max_retries=3, sleep=waits.append)
    result = gateway.complete([{"role": "user", "content": "x"}],
                              tier="debater", temperature=0.5, max_tokens=64)
    assert result.text == "恢复"
    assert waits == [90.0, 90.0]  # 限流退避 90s（v1.4.2：扛过免费档惩罚期）


def test_gateway_short_backoff_on_other_errors():
    waits: list[float] = []

    class Flaky:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("瞬态故障")
            return "OK"

    gateway = Gateway(backend=Flaky(), model_debater="d", model_official="o",
                      max_retries=3, sleep=waits.append)
    assert gateway.complete([{"role": "user", "content": "x"}],
                            tier="debater", temperature=0.5, max_tokens=64).text == "OK"
    assert waits == [1.0]  # 普通错误仍指数退避


# ---- ISS-015 #2：不可重试错误 fail-fast（内容过滤 / 参数/鉴权类 4xx 首打即抛）----

FILTER_MSG = (
    "Error code: 400 - {'contentFilter': [{'level': 1, 'role': 'user'}], "
    "'error': {'code': '1301', 'message': '系统检测到不安全内容'}}"
)


class FakeContentFilterError(Exception):
    """模拟 openai.BadRequestError 的 contentFilter 400（按消息匹配，不依赖 openai 导入）。"""


class FakeBadRequestError(Exception):
    """模拟 openai.BadRequestError（按类名匹配）。"""


def test_content_filtered_is_llm_exhausted_subclass():
    """不认识 ContentFiltered 的既有调用方仍按 LLMExhausted 占位降级兜底（SPEC §4.9）。"""
    assert issubclass(ContentFiltered, LLMExhausted)


def test_gateway_fail_fast_on_content_filter_no_retry_no_backoff():
    """内容过滤：首打即抛 ContentFiltered——不进 3 次重试、不烧退避（修复前 4 连打白烧）。"""
    class Filtered:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            raise FakeContentFilterError(FILTER_MSG)

    waits: list[float] = []
    backend = Filtered()
    gateway = Gateway(backend=backend, model_debater="d", model_official="o",
                      max_retries=3, sleep=waits.append)
    with pytest.raises(ContentFiltered):
        gateway.complete(MSG, tier="debater", temperature=0.5, max_tokens=64)
    assert backend.calls == 1
    assert waits == []


def test_gateway_fail_fast_on_content_filter_stream():
    """流式路径与 complete 同语义：内容过滤首打即抛。"""
    class FilteredStream:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            return "不应走到这里"

        def stream(self, messages, **kwargs):
            self.calls += 1
            raise FakeContentFilterError(FILTER_MSG)  # 400 在建连时即拒，先于任何 chunk

    waits: list[float] = []
    backend = FilteredStream()
    gateway = Gateway(backend=backend, model_debater="d", model_official="o",
                      max_retries=3, sleep=waits.append)
    with pytest.raises(ContentFiltered):
        list(gateway.stream(MSG, tier="debater", temperature=0.5, max_tokens=64))
    assert backend.calls == 1
    assert waits == []


def test_gateway_fail_fast_on_fatal_4xx_class_name():
    """参数/鉴权类 4xx（按异常类名识别）：抛 LLMExhausted 且不重试、不退避。"""
    class BadAuth:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            raise FakeBadRequestError("invalid api key")

    waits: list[float] = []
    backend = BadAuth()
    gateway = Gateway(backend=backend, model_debater="d", model_official="o",
                      max_retries=3, sleep=waits.append)
    with pytest.raises(LLMExhausted) as exc_info:
        gateway.complete(MSG, tier="debater", temperature=0.5, max_tokens=64)
    assert not isinstance(exc_info.value, ContentFiltered)  # 非过滤，不给引擎去证据回退信号
    assert "不可重试" in str(exc_info.value)
    assert backend.calls == 1
    assert waits == []


def test_gateway_fail_fast_on_fatal_4xx_error_code_message():
    """按消息形态识别（裸 RuntimeError 模拟未导入 openai 类名的场景）。"""
    class BadRequest:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            raise RuntimeError("Error code: 422 - {'error': 'Unprocessable Entity'}")

    backend = BadRequest()
    gateway = Gateway(backend=backend, model_debater="d", model_official="o",
                      max_retries=3, sleep=lambda s: None)
    with pytest.raises(LLMExhausted):
        gateway.complete(MSG, tier="debater", temperature=0.5, max_tokens=64)
    assert backend.calls == 1


def test_gateway_transient_429_and_5xx_still_retry():
    """回归护栏：fail-fast 不误伤可重试错误——429 与 5xx 仍走满重试与长退避。"""
    class FlakyTransient:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise FakeRateLimitError("429 Too Many Requests")
            if self.calls == 2:
                raise RuntimeError("Error code: 500 - server error")
            return "恢复"

    waits: list[float] = []
    backend = FlakyTransient()
    gateway = Gateway(backend=backend, model_debater="d", model_official="o",
                      max_retries=3, sleep=waits.append)
    result = gateway.complete(MSG, tier="debater", temperature=0.5, max_tokens=64)
    assert result.text == "恢复"
    assert backend.calls == 3
    assert waits == [90.0, 2.0]  # 429 长退避 + 5xx 指数退避，均未被 fail-fast 截断


def test_gateway_throttles_by_min_interval():
    """全局限速（v1.4）：相邻调用间隔不足 min_interval 时自动等待。"""
    import time as _time

    fake = FakeLLM(mode="echo")
    waits: list[float] = []

    class Clock:
        now = 1000.0

    def fake_sleep(seconds: float) -> None:
        waits.append(round(seconds, 2))
        Clock.now += seconds  # 时间随等待推进

    gateway = Gateway(backend=fake, model_debater="d", model_official="o",
                      min_interval=12.0, sleep=fake_sleep)
    orig_monotonic = _time.monotonic
    _time.monotonic = lambda: Clock.now
    try:
        gateway.complete([{"role": "user", "content": "a"}], tier="debater", temperature=0.5, max_tokens=8)
        gateway.complete([{"role": "user", "content": "b"}], tier="debater", temperature=0.5, max_tokens=8)  # 间隔 0 → 应等待 12s
        gateway.complete([{"role": "user", "content": "c"}], tier="debater", temperature=0.5, max_tokens=8)
    finally:
        _time.monotonic = orig_monotonic
    assert waits == [12.0, 12.0]  # 每次紧邻调用都补足 12s


# ---- v2.6.2 文本型工具调用：解析 / 剥离 / 原生循环回注 / 流式压制 ----

BUG_SHAPE = (
    "<tool_call>\nsearch_evidence\n<arg_key>query</arg_key>\n"
    "<arg_value>亲密关系 相互性 满意度</arg_value>\n</tool_call>"
)


def test_parse_text_tool_calls_xml_pair_form():
    parsed = parse_text_tool_calls(BUG_SHAPE)
    assert parsed == [("search_evidence", {"query": "亲密关系 相互性 满意度"})]


def test_parse_text_tool_calls_json_form():
    text = '<tool_call>\n{"name": "search_web", "arguments": {"query": "AI"}}\n</tool_call>'
    assert parse_text_tool_calls(text) == [("search_web", {"query": "AI"})]


def test_parse_text_tool_calls_none_when_clean():
    assert parse_text_tool_calls("我方认为技术是人的延伸。") is None


def test_strip_tool_artifacts_removes_leak():
    assert strip_tool_artifacts(BUG_SHAPE) == ""
    leaked = "search_evidence\n<arg_key>query</arg_key>\n<arg_value>测试</arg_value>\n</tool_call>"
    assert strip_tool_artifacts(leaked) == ""
    mixed = "我方认为技术是人的延伸。\n" + BUG_SHAPE
    assert strip_tool_artifacts(mixed) == "我方认为技术是人的延伸。"


class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeOpenAIClient:
    """最小 OpenAI 客户端替身：按序返回预置响应，记录调用参数。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

        class _API:
            completions = self

        self.chat = _API()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = self._responses.pop(0)

        class _Resp:
            usage = None

            def __init__(self, message):
                self.choices = [type("C", (), {"message": message})()]

        return _Resp(message)


def test_chat_with_tools_executes_text_form_tool_call():
    """截图 bug 回归：正文里的伪 XML 调用必须被执行回注，而不是当发言泄漏。"""
    client = _FakeOpenAIClient(
        [_Msg(content=BUG_SHAPE), _Msg(content="我方认为亲密关系中的相互性决定满意度。")]
    )
    gateway = Gateway(backend=None, model_debater="d", model_official="o")
    gateway._client = client
    executed: list[tuple[str, dict]] = []
    result = gateway.chat_with_tools(
        [{"role": "user", "content": "发言"}],
        tools=[{"type": "function", "function": {"name": "search_evidence", "parameters": {}}}],
        execute=lambda name, args: (executed.append((name, args)), "找到 3 条证据")[1],
        tier="debater", temperature=0.5, max_tokens=256,
    )
    assert executed == [("search_evidence", {"query": "亲密关系 相互性 满意度"})]
    assert result.text == "我方认为亲密关系中的相互性决定满意度。"
    # 回注的对话是协议合法的：assistant.tool_calls + role=tool 成对出现
    convo = client.calls[1]["messages"]
    assert any(m.get("tool_calls") for m in convo)
    assert any(m.get("role") == "tool" for m in convo)


def test_complete_strips_tool_artifacts_from_backend():
    class Leaky:
        def complete(self, messages, **kwargs):
            return BUG_SHAPE

    gateway = Gateway(backend=Leaky(), model_debater="d", model_official="o")
    assert gateway.complete(MSG, tier="debater", temperature=0.5, max_tokens=64).text == ""


def test_stream_suppresses_tool_call_region():
    """流式路径：工具调用区域跨 chunk 出现也必须整体压制，正文照常透传。"""
    pieces = ["我方认为", "<tool", "_call>\nsearch_evidence\n<arg_key>query</arg", "_key>\n"
              "<arg_value>测试</arg_value>\n</tool_call>", "技术是延伸", "。"]
    backend = type("B", (), {"complete": staticmethod(lambda *a, **k: ""), "stream": staticmethod(lambda *a, **k: iter(pieces))})()
    gateway = Gateway(backend=backend, model_debater="d", model_official="o")
    chunks = list(gateway.stream(MSG, tier="debater", temperature=0.5, max_tokens=64))
    assert "".join(chunks) == "我方认为技术是延伸。"


def test_stream_tool_filter_chunk_boundaries():
    flt = _StreamToolFilter()
    out = flt.feed("前文<tool") + flt.feed("_call>垃圾内") + flt.feed("容</tool_call>后文")
    out += flt.flush()
    assert out == "前文后文"
