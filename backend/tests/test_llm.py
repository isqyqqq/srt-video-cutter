"""模型调用层测试：错误提示可读性、流式解析、超时回退。"""

from __future__ import annotations

import httpx
import pytest

from app import llm

CLOUDFLARE_HTML = """<!DOCTYPE html>
<!--[if lt IE 7]> <html class="no-js ie6 oldie" lang="en-US"> <![endif]-->
<html><head><title>loxo.cc | 524: A timeout occurred</title></head>
<body>Error code 524</body></html>"""


def test_gateway_timeout_message_hides_html():
    """524 要给中文原因，绝不能把 Cloudflare 的 HTML 甩给用户。"""

    message = llm._http_error_message(524, CLOUDFLARE_HTML)

    assert "网关超时" in message
    assert "524" in message
    assert "DOCTYPE" not in message
    assert "<html" not in message


def test_gateway_timeout_codes_cover_cloudflare_range():
    assert 524 in llm.GATEWAY_TIMEOUT_CODES
    assert 520 in llm.GATEWAY_TIMEOUT_CODES


def test_error_detail_reads_openai_json_error():
    body = '{"error": {"message": "model not found"}}'
    assert llm._error_detail(body) == "model not found"
    assert "model not found" in llm._http_error_message(400, body)


def test_plain_text_error_is_truncated():
    detail = llm._error_detail("x" * 500)
    assert len(detail) == 300


def test_sse_content_joins_deltas():
    lines = [
        'data: {"choices":[{"delta":{"role":"assistant"}}]}',
        "",
        'data: {"choices":[{"delta":{"content":"{\\"a\\":"}}]}',
        'data: {"choices":[{"delta":{"content":"1}"}}]}',
        "data: [DONE]",
    ]
    assert llm._sse_content(lines) == '{"a":1}'


def test_sse_content_ignores_broken_lines():
    lines = ["data: 不是 JSON", "data:", ": keep-alive", 'data: {"choices":[]}']
    assert llm._sse_content(lines) == ""


def _config() -> llm.LlmConfig:
    return llm.LlmConfig(
        provider="openai",
        base_url="https://example.test/v1",
        model="fake",
        api_key="k",
        timeout=30.0,
    )


def test_post_openai_streams_by_default(monkeypatch):
    """默认走流式：只应看到一次带 stream=true 的请求。"""

    seen: list[dict] = []

    def fake_stream(method, url, headers=None, json=None, timeout=None):
        seen.append(json)

        class _Ctx:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_lines(self):
                return iter(['data: {"choices":[{"delta":{"content":"ok"}}]}'])

        return _Ctx()

    monkeypatch.setattr(llm.httpx, "stream", fake_stream)
    monkeypatch.delenv("LLM_STREAM", raising=False)

    assert llm._post_openai([{"role": "user", "content": "hi"}], _config()) == "ok"
    assert len(seen) == 1
    assert seen[0]["stream"] is True
    assert seen[0]["response_format"] == {"type": "json_object"}


def test_post_openai_falls_back_when_stream_unsupported(monkeypatch):
    """服务不支持 stream 时（400），要自动退回非流式再成功。"""

    posts: list[dict] = []

    def fake_stream(method, url, headers=None, json=None, timeout=None):
        class _Ctx:
            status_code = 400

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"error": {"message": "stream not supported"}}'

        return _Ctx()

    def fake_post(url, headers=None, json=None, timeout=None):
        posts.append(json)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "non-stream ok"}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(llm.httpx, "stream", fake_stream)
    monkeypatch.setattr(llm.httpx, "post", fake_post)

    text = llm._post_openai([{"role": "user", "content": "hi"}], _config())

    assert text == "non-stream ok"
    assert posts and "stream" not in posts[0]


def test_post_openai_retries_when_stream_returns_empty(monkeypatch):
    """流式 200 但没正文时，应继续尝试，最终拿到非流式结果。"""

    def fake_stream(method, url, headers=None, json=None, timeout=None):
        class _Ctx:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_lines(self):
                return iter([])  # 空流

        return _Ctx()

    def fake_post(url, headers=None, json=None, timeout=None):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "fallback ok"}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(llm.httpx, "stream", fake_stream)
    monkeypatch.setattr(llm.httpx, "post", fake_post)

    assert llm._post_openai([{"role": "user", "content": "hi"}], _config()) == "fallback ok"


def test_post_openai_raises_friendly_error_on_524(monkeypatch):
    """524 直接抛中文提示，不做无意义重试。"""

    calls: list[str] = []

    def fake_stream(method, url, headers=None, json=None, timeout=None):
        calls.append(url)

        class _Ctx:
            status_code = 524

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return CLOUDFLARE_HTML.encode("utf-8")

        return _Ctx()

    monkeypatch.setattr(llm.httpx, "stream", fake_stream)

    with pytest.raises(llm.LlmError) as excinfo:
        llm._post_openai([{"role": "user", "content": "hi"}], _config())

    message = str(excinfo.value)
    assert "网关超时" in message
    assert "DOCTYPE" not in message
    assert len(calls) == 1


def test_post_openai_retries_once_on_broken_stream(monkeypatch):
    """流式读取中途断开（incomplete chunked read）时应自动重试一次并成功。"""

    calls = {"n": 0}

    def fake_stream(method, url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        attempt = calls["n"]

        class _Ctx:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_lines(self):
                if attempt == 1:

                    def broken():
                        yield 'data: {"choices":[{"delta":{"content":"半截"}}]}'
                        raise httpx.RemoteProtocolError("incomplete chunked read")

                    return broken()
                return iter(['data: {"choices":[{"delta":{"content":"完整"}}]}'])

        return _Ctx()

    monkeypatch.setattr(llm.httpx, "stream", fake_stream)

    assert llm._post_openai([{"role": "user", "content": "hi"}], _config()) == "完整"
    assert calls["n"] == 2


def test_post_openai_reports_broken_stream_after_retry(monkeypatch):
    """两次都断时，给出可读中文提示，而不是原始异常。"""

    def fake_stream(method, url, headers=None, json=None, timeout=None):
        class _Ctx:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_lines(self):
                def broken():
                    raise httpx.RemoteProtocolError("incomplete chunked read")
                    yield  # pragma: no cover

                return broken()

        return _Ctx()

    monkeypatch.setattr(llm.httpx, "stream", fake_stream)

    with pytest.raises(llm.LlmError) as excinfo:
        llm._post_openai([{"role": "user", "content": "hi"}], _config())

    message = str(excinfo.value)
    assert "中途断开" in message
    assert "incomplete chunked read" not in message


def test_post_openai_reports_connect_error_clearly(monkeypatch):
    """连不上时给“检查网络/LLM_BASE_URL”的提示。"""

    def fake_stream(method, url, headers=None, json=None, timeout=None):
        raise httpx.ConnectError("nodename nor servname provided")

    monkeypatch.setattr(llm.httpx, "stream", fake_stream)

    with pytest.raises(llm.LlmError) as excinfo:
        llm._post_openai([{"role": "user", "content": "hi"}], _config())

    assert "无法连接模型服务" in str(excinfo.value)
    assert "LLM_BASE_URL" in str(excinfo.value)
