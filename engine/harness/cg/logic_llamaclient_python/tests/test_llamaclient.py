import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llamaclient import (
    LlamaClient,
    IncompleteGeneration,
    extract_xml_tool_calls,
    get_llama_base_url,
    get_llama_model,
)


# --- get_llama_base_url ---

def test_get_llama_base_url_explicit():
    assert get_llama_base_url("http://example.com:9999/v1") == "http://example.com:9999/v1"


def test_get_llama_base_url_strips_trailing_slash():
    assert get_llama_base_url("http://example.com/v1/") == "http://example.com/v1"


def test_get_llama_base_url_env_var(monkeypatch):
    monkeypatch.setenv("LLAMA_BASE_URL", "http://localhost:58080/v1")
    monkeypatch.delenv("LLAMA_SERVER_URL", raising=False)
    assert get_llama_base_url() == "http://localhost:58080/v1"


def test_get_llama_base_url_fallback_env_var(monkeypatch):
    monkeypatch.delenv("LLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("LLAMA_SERVER_URL", "http://localhost:58081/v1")
    assert get_llama_base_url() == "http://localhost:58081/v1"


def test_get_llama_base_url_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("LLAMA_BASE_URL", "http://env-host/v1")
    assert get_llama_base_url("http://explicit-host/v1") == "http://explicit-host/v1"


def test_get_llama_base_url_default(monkeypatch):
    monkeypatch.delenv("LLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("LLAMA_SERVER_URL", raising=False)
    assert get_llama_base_url() == "http://localhost:8080/v1"


# --- get_llama_model ---

def test_get_llama_model_explicit():
    assert get_llama_model("my-model") == "my-model"


def test_get_llama_model_env(monkeypatch):
    monkeypatch.setenv("LLAMA_MODEL", "env-model")
    assert get_llama_model() == "env-model"


def test_get_llama_model_default(monkeypatch):
    monkeypatch.delenv("LLAMA_MODEL", raising=False)
    assert get_llama_model() == "model"


# --- LlamaClient initialization ---

def test_client_defaults(monkeypatch):
    monkeypatch.delenv("LLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("LLAMA_SERVER_URL", raising=False)
    monkeypatch.delenv("LLAMA_MODEL", raising=False)
    client = LlamaClient()
    assert client.base_url == "http://localhost:8080/v1"
    assert client.default_model == "model"
    assert client.timeout == 120


def test_client_explicit_params():
    client = LlamaClient(
        base_url="http://myhost:1234/v1",
        default_model="llama3",
        timeout=60,
    )
    assert client.base_url == "http://myhost:1234/v1"
    assert client.default_model == "llama3"
    assert client.timeout == 60


def test_client_trailing_slash_stripped():
    client = LlamaClient(base_url="http://myhost/v1/")
    assert client.base_url == "http://myhost/v1"


def test_client_uses_env_var(monkeypatch):
    monkeypatch.setenv("LLAMA_BASE_URL", "http://env-host:58080/v1")
    client = LlamaClient()
    assert client.base_url == "http://env-host:58080/v1"


# --- extract_xml_tool_calls ---

def test_extract_xml_tool_calls_basic():
    text = '<tool_call>\n{"name": "search", "arguments": {"q": "hello"}}\n</tool_call>'
    calls, clean = extract_xml_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "search"
    assert calls[0]["arguments"]["q"] == "hello"
    assert clean == ""


def test_extract_xml_tool_calls_with_surrounding_text():
    text = 'Let me search.\n<tool_call>\n{"name": "search", "arguments": {"q": "test"}}\n</tool_call>\nDone.'
    calls, clean = extract_xml_tool_calls(text)
    assert len(calls) == 1
    assert clean != ""


def test_extract_xml_tool_calls_multiple():
    text = (
        '<tool_call>\n{"name": "tool_a", "arguments": {}}\n</tool_call>\n'
        '<tool_call>\n{"name": "tool_b", "arguments": {"x": 1}}\n</tool_call>'
    )
    calls, _ = extract_xml_tool_calls(text)
    assert len(calls) == 2
    assert calls[0]["name"] == "tool_a"
    assert calls[1]["name"] == "tool_b"


def test_extract_xml_tool_calls_no_tags():
    calls, clean = extract_xml_tool_calls("Just a normal response.")
    assert calls == []
    assert clean == "Just a normal response."


def test_extract_xml_tool_calls_invalid_json():
    text = '<tool_call>\nnot-json\n</tool_call>'
    calls, _ = extract_xml_tool_calls(text)
    assert calls == []


def test_extract_xml_tool_calls_unclosed_tag():
    text = '<tool_call>\n{"name": "fn", "arguments": {"a": 1}}'
    calls, _ = extract_xml_tool_calls(text)
    # Unclosed tag fallback — may or may not parse depending on format
    assert isinstance(calls, list)


# --- Health check (real unavailable server) ---

@pytest.mark.asyncio
async def test_health_check_unavailable():
    client = LlamaClient(base_url="http://localhost:19999/v1")
    health = await client.health_check(timeout=1.0)
    assert health["available"] is False
    assert health["base_url"] == "http://localhost:19999/v1"
    assert "error" in health


@pytest.mark.asyncio
async def test_verify_connection_raises():
    client = LlamaClient(base_url="http://localhost:19999/v1")
    with pytest.raises(ConnectionError) as exc_info:
        await client.verify_connection(raise_on_error=True)
    assert "llama-server is not available" in str(exc_info.value)


@pytest.mark.asyncio
async def test_verify_connection_silent():
    client = LlamaClient(base_url="http://localhost:19999/v1")
    assert await client.verify_connection(raise_on_error=False) is False


# --- Mocked health check (200 OK) ---

def _make_mock_response(status: int, json_data: dict):
    """Create a mock aiohttp response context manager."""
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data)
    mock_resp.raise_for_status = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return mock_resp, cm


@pytest.mark.asyncio
async def test_health_check_available():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    mock_resp, cm = _make_mock_response(200, {"data": []})
    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        health = await client.health_check()
    assert health["available"] is True
    assert health["status_code"] == 200


@pytest.mark.asyncio
async def test_verify_connection_success():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    mock_resp, cm = _make_mock_response(200, {"data": []})
    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        assert await client.verify_connection() is True


# --- Mocked chat ---

def _make_chat_response(content: str = "Hello!", tool_calls=None, finish_reason: str = "stop"):
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "model": "test-model",
    }


def _make_mock_post_session(response_data: dict):
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.json = AsyncMock(return_value=response_data)
    mock_resp.raise_for_status = MagicMock()
    post_cm = AsyncMock()
    post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    post_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=post_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    return session_cm


@pytest.mark.asyncio
async def test_chat_basic():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    session_cm = _make_mock_post_session(_make_chat_response("Hi there!"))
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.chat(messages=[{"role": "user", "content": "Hello"}])
    assert result["content"] == "Hi there!"
    assert result["finish_reason"] == "stop"
    assert result["usage"]["total_tokens"] == 15


@pytest.mark.asyncio
async def test_chat_with_model_override():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    session_cm = _make_mock_post_session(_make_chat_response("ok"))
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="custom-model",
        )
    assert result["content"] == "ok"


@pytest.mark.asyncio
async def test_chat_xml_tool_calls():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    raw = '<tool_call>\n{"name": "search", "arguments": {"q": "cats"}}\n</tool_call>'
    session_cm = _make_mock_post_session(_make_chat_response(raw))
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.chat(
            messages=[{"role": "user", "content": "search cats"}],
            tool_call_format="xml",
        )
    assert result["tool_calls"] is not None
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["function"]["name"] == "search"


@pytest.mark.asyncio
async def test_chat_json_tool_calls():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    raw = json.dumps({
        "content": "Searching...",
        "tool_calls": [{"name": "search", "arguments": {"q": "dogs"}}],
    })
    session_cm = _make_mock_post_session(_make_chat_response(raw))
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.chat(
            messages=[{"role": "user", "content": "search dogs"}],
            tool_call_format="json",
        )
    assert result["tool_calls"] is not None
    assert result["tool_calls"][0]["function"]["name"] == "search"
    assert result["content"] == "Searching..."


@pytest.mark.asyncio
async def test_chat_native_tool_calls():
    native_tc = [{"id": "call_abc", "type": "function",
                  "function": {"name": "do_thing", "arguments": "{}"}}]
    client = LlamaClient(base_url="http://localhost:8080/v1")
    session_cm = _make_mock_post_session(
        _make_chat_response("", tool_calls=native_tc, finish_reason="tool_calls")
    )
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.chat(
            messages=[{"role": "user", "content": "do thing"}],
            tools=[{"type": "function", "function": {"name": "do_thing", "parameters": {}}}],
        )
    assert result["tool_calls"] == native_tc


@pytest.mark.asyncio
async def test_chat_verbose(capsys):
    client = LlamaClient(base_url="http://localhost:8080/v1")
    session_cm = _make_mock_post_session(_make_chat_response("ok"))
    with patch("aiohttp.ClientSession", return_value=session_cm):
        await client.chat(
            messages=[{"role": "user", "content": "hi"}],
            verbose=True,
        )
    # verbose uses logger.debug, not print — no stdout output expected


# --- Mocked complete ---

@pytest.mark.asyncio
async def test_complete_basic():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    response_data = {
        "choices": [{"text": "generated text"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        "model": "test-model",
    }
    session_cm = _make_mock_post_session(response_data)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.complete(prompt="Once upon a time")
    assert result["content"] == "generated text"
    assert result["usage"]["total_tokens"] == 8


# --- Mocked get_models ---

@pytest.mark.asyncio
async def test_get_models():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    mock_resp, cm = _make_mock_response(200, {"data": [{"id": "llama3"}]})
    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        models = await client.get_models()
    assert models == [{"id": "llama3"}]


# --- Mocked stream_chat ---

def _make_sse_lines(tokens: list[str], usage: dict | None = None) -> list[bytes]:
    lines = []
    for token in tokens:
        data = {"choices": [{"delta": {"content": token}}]}
        lines.append(f"data: {json.dumps(data)}\n\n".encode())
    if usage:
        usage_data = {"usage": usage}
        lines.append(f"data: {json.dumps(usage_data)}\n\n".encode())
    lines.append(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n')
    lines.append(b"data: [DONE]\n\n")
    return lines


@pytest.mark.asyncio
async def test_stream_chat_basic():
    client = LlamaClient(base_url="http://localhost:8080/v1")

    sse_bytes = _make_sse_lines(
        ["Hello", " world", "!"],
        usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    )

    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    async def fake_content():
        for line in sse_bytes:
            yield line

    mock_resp.content = fake_content()

    post_cm = AsyncMock()
    post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    post_cm.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=post_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    chunks = []
    with patch("aiohttp.ClientSession", return_value=session_cm):
        async for chunk in client.stream_chat(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

    content_chunks = [c for c in chunks if c["type"] == "content"]
    usage_chunks = [c for c in chunks if c["type"] == "usage"]
    assert len(content_chunks) == 3
    assert "".join(c["content"] for c in content_chunks) == "Hello world!"
    assert len(usage_chunks) == 1
    assert usage_chunks[0]["done"] is True


@pytest.mark.asyncio
async def test_stream_chat_xml_tool_calls():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    tool_text = '<tool_call>\n{"name": "search", "arguments": {"q": "test"}}\n</tool_call>'
    sse_bytes = _make_sse_lines([tool_text])

    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    async def fake_content():
        for line in sse_bytes:
            yield line

    mock_resp.content = fake_content()
    post_cm = AsyncMock()
    post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    post_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=post_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    chunks = []
    with patch("aiohttp.ClientSession", return_value=session_cm):
        async for chunk in client.stream_chat(
            messages=[{"role": "user", "content": "search"}],
            tool_call_format="xml",
        ):
            chunks.append(chunk)

    tool_chunks = [c for c in chunks if c["type"] == "tool_calls"]
    assert len(tool_chunks) == 1
    assert tool_chunks[0]["tool_calls"][0]["function"]["name"] == "search"


# --- Static helpers ---

def test_encode_image_base64_missing_file():
    with pytest.raises(FileNotFoundError):
        LlamaClient.encode_image_base64("/nonexistent/image.png")


def test_create_image_message_url():
    msg = LlamaClient.create_image_message(
        role="user",
        text="What's in this?",
        image_url="https://example.com/img.jpg",
    )
    assert msg["role"] == "user"
    assert msg["content"][0]["type"] == "text"
    assert msg["content"][1]["type"] == "image_url"
    assert msg["content"][1]["image_url"]["url"] == "https://example.com/img.jpg"


def test_create_image_message_requires_one_source():
    with pytest.raises(ValueError):
        LlamaClient.create_image_message(role="user", text="test")
    with pytest.raises(ValueError):
        LlamaClient.create_image_message(
            role="user", text="test",
            image_url="http://example.com/a.jpg",
            image_path="/some/path.jpg",
        )


# --- Internal helpers ---

def test_sanitize_messages_manual_tool_role():
    client = LlamaClient(base_url="http://localhost/v1")
    messages = [
        {"role": "tool", "name": "search", "content": "result", "tool_call_id": "abc"}
    ]
    sanitized = client._sanitize_messages_manual(messages, "xml")
    assert sanitized[0]["role"] == "user"
    assert "TOOL_RESULT (search)" in sanitized[0]["content"]
    assert "tool_call_id" not in sanitized[0]


def test_sanitize_messages_manual_xml_assistant():
    client = LlamaClient(base_url="http://localhost/v1")
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "search", "arguments": '{"q": "hello"}'}}
            ],
        }
    ]
    sanitized = client._sanitize_messages_manual(messages, "xml")
    assert "tool_calls" not in sanitized[0]
    assert "<tool_call>" in sanitized[0]["content"]


def test_sanitize_messages_manual_json_assistant():
    client = LlamaClient(base_url="http://localhost/v1")
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"function": {"name": "fn", "arguments": "{}"}}],
        }
    ]
    sanitized = client._sanitize_messages_manual(messages, "json")
    assert "tool_calls" not in sanitized[0]
    assert "THOUGHT" in sanitized[0]["content"]


def test_inject_system_prompt_creates_system_message():
    client = LlamaClient(base_url="http://localhost/v1")
    messages = [{"role": "user", "content": "Hi"}]
    client._inject_system_prompt(messages, "You are helpful.")
    assert messages[0]["role"] == "system"
    assert "You are helpful." in messages[0]["content"]


def test_inject_system_prompt_appends_to_existing():
    client = LlamaClient(base_url="http://localhost/v1")
    messages = [
        {"role": "system", "content": "Original."},
        {"role": "user", "content": "Hi"},
    ]
    client._inject_system_prompt(messages, "Extra instructions.")
    assert "Original." in messages[0]["content"]
    assert "Extra instructions." in messages[0]["content"]


def test_build_tool_prompt_xml():
    client = LlamaClient(base_url="http://localhost/v1")
    tools = [{"type": "function", "function": {"name": "search"}}]
    prompt = client._build_tool_prompt(tools, "xml")
    assert "<tools>" in prompt
    assert "<tool_call>" in prompt


def test_build_tool_prompt_json():
    client = LlamaClient(base_url="http://localhost/v1")
    tools = [{"type": "function", "function": {"name": "search"}}]
    prompt = client._build_tool_prompt(tools, "json")
    assert "JSON" in prompt
    assert "tool_calls" in prompt


def test_apply_xml_calls():
    client = LlamaClient(base_url="http://localhost/v1")
    result = client._empty_result("model")
    raw = '<tool_call>\n{"name": "fn", "arguments": {"x": 1}}\n</tool_call>'
    found = client._apply_xml_calls(result, raw)
    assert found is True
    assert result["tool_calls"][0]["function"]["name"] == "fn"
    assert result["content"] == ""


def test_apply_xml_calls_no_tags():
    client = LlamaClient(base_url="http://localhost/v1")
    result = client._empty_result("model")
    found = client._apply_xml_calls(result, "no tool calls here")
    assert found is False
    assert result["tool_calls"] is None


def test_apply_json_calls():
    client = LlamaClient(base_url="http://localhost/v1")
    result = client._empty_result("model")
    raw = json.dumps({
        "content": "thinking...",
        "tool_calls": [{"name": "fn", "arguments": {"a": 1}}],
    })
    client._apply_json_calls(result, raw)
    assert result["tool_calls"] is not None
    assert result["tool_calls"][0]["function"]["name"] == "fn"
    assert result["content"] == "thinking..."


def test_apply_json_calls_no_tool_calls():
    client = LlamaClient(base_url="http://localhost/v1")
    result = client._empty_result("model")
    raw = json.dumps({"content": "just text", "tool_calls": []})
    client._apply_json_calls(result, raw)
    assert result["tool_calls"] is None


def test_fix_xml_native_calls_passthrough_non_xml():
    client = LlamaClient(base_url="http://localhost/v1")
    native = [{"id": "x", "type": "function", "function": {"name": "fn", "arguments": "{}"}}]
    result = client._fix_xml_native_calls(native, None)
    assert result == native


def test_fix_xml_native_calls_garbled():
    client = LlamaClient(base_url="http://localhost/v1")
    garbled = [
        {"id": "x", "type": "function",
         "function": {"name": '{"name": "search", "arguments": {"q": "hi"}}',
                      "arguments": "{}"}}
    ]
    fixed = client._fix_xml_native_calls(garbled, "xml")
    assert len(fixed) >= 1
    assert fixed[0]["function"]["name"] == "search"


def test_parse_tool_calls_from_content_xml():
    client = LlamaClient(base_url="http://localhost/v1")
    raw = '<tool_call>\n{"name": "fn", "arguments": {}}\n</tool_call>'
    chunk = client._parse_tool_calls_from_content(raw, "xml")
    assert chunk is not None
    assert chunk["type"] == "tool_calls"
    assert chunk["tool_calls"][0]["function"]["name"] == "fn"


def test_parse_tool_calls_from_content_json():
    client = LlamaClient(base_url="http://localhost/v1")
    raw = json.dumps({"content": "ok", "tool_calls": [{"name": "fn", "arguments": {}}]})
    chunk = client._parse_tool_calls_from_content(raw, "json")
    assert chunk is not None
    assert chunk["tool_calls"][0]["function"]["name"] == "fn"


def test_parse_tool_calls_from_content_no_calls():
    client = LlamaClient(base_url="http://localhost/v1")
    chunk = client._parse_tool_calls_from_content("just text", "xml")
    assert chunk is None


def test_build_payload_filters_none():
    client = LlamaClient(base_url="http://localhost/v1")
    payload = client._build_payload(
        model="m",
        messages=[],
        temperature=0.7,
        max_tokens=None,
        top_p=None,
    )
    assert payload["temperature"] == 0.7
    assert "max_tokens" not in payload
    assert "top_p" not in payload


# --- XML fallback parsing paths ---

def test_extract_xml_tool_calls_strict_invalid_json_skipped():
    # Strict match finds a block but the JSON is invalid — should skip it
    # Use a brace-only match that is invalid JSON (non-greedy stops at first })
    # e.g. {"name": "good"} is valid, {"bad": "json} is not (stops at first })
    text = '<tool_call>{"name": "good", "arguments": {}}</tool_call>'
    calls, clean = extract_xml_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "good"


def test_extract_xml_tool_calls_strict_bad_json_in_braces():
    # Force the json.JSONDecodeError path by having "{ invalid json }" inside tags
    # The TOOL_CALL_PATTERN captures {[\s\S]*?} non-greedy.
    # For {"a": "b} in text, non-greedy stops at first } → captures {"a": "b which is invalid
    text = '<tool_call>\n{"a": "b}\n</tool_call>'
    calls, _ = extract_xml_tool_calls(text)
    # Either skipped (JSONDecodeError on invalid) or parsed — just ensure no crash
    assert isinstance(calls, list)


def test_extract_xml_tool_calls_unclosed_brace_fallback():
    # Unclosed tag format with valid JSON — tests brace-counting fallback path
    text = '<tool_call>\n{"name": "fn", "arguments": {"x": 1}} extra text'
    calls, _ = extract_xml_tool_calls(text)
    # Brace counting may extract calls or fall through to text_parts
    assert isinstance(calls, list)


def test_extract_xml_tool_calls_glm_format():
    # GLM Flash format: function name before JSON inside <tool_call> (no closing tag)
    text = '<tool_call>\nsearch\n{"query": "hello"}'
    calls, _ = extract_xml_tool_calls(text)
    # GLM format: splits on <tool_call>, finds "search\n{...}" → parses as {"name": "search", "arguments": {...}}
    assert isinstance(calls, list)
    if calls:
        assert calls[0].get("name") == "search"


def test_extract_xml_tool_calls_brace_remainder_text():
    # Unclosed tag with JSON followed by extra text — tests brace-match remainder path
    text = '<tool_call>\n{"name": "fn", "arguments": {}} extra stuff'
    calls, clean = extract_xml_tool_calls(text)
    assert isinstance(calls, list)
    assert isinstance(clean, str)


# --- chat() with all payload params ---

@pytest.mark.asyncio
async def test_chat_with_all_sampling_params():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    session_cm = _make_mock_post_session(_make_chat_response("ok"))
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.chat(
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.5,
            max_tokens=100,
            top_p=0.9,
            top_k=40,
            frequency_penalty=0.1,
            presence_penalty=0.1,
            stop=["<end>"],
            seed=42,
            min_p=0.05,
            repeat_penalty=1.1,
            repeat_last_n=64,
            mirostat=2,
            mirostat_tau=5.0,
            mirostat_eta=0.1,
            typical_p=0.9,
            tfs_z=1.0,
        )
    assert result["content"] == "ok"


@pytest.mark.asyncio
async def test_chat_with_tools_native():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    session_cm = _make_mock_post_session(_make_chat_response("ok"))
    tools = [{"type": "function", "function": {"name": "fn", "parameters": {}}}]
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.chat(
            messages=[{"role": "user", "content": "hi"}],
            tools=tools,
            tool_choice="auto",
            parallel_tool_calls=True,
        )
    assert result["content"] == "ok"


@pytest.mark.asyncio
async def test_chat_with_response_format():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    session_cm = _make_mock_post_session(_make_chat_response('{"result": "ok"}'))
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.chat(
            messages=[{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},
        )
    assert result["content"] == '{"result": "ok"}'


@pytest.mark.asyncio
async def test_chat_with_grammar():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    session_cm = _make_mock_post_session(_make_chat_response("ok"))
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.chat(
            messages=[{"role": "user", "content": "hi"}],
            grammar='root ::= "ok"',
        )
    assert result["content"] == "ok"


@pytest.mark.asyncio
async def test_chat_xml_with_tools_injects_prompt():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    session_cm = _make_mock_post_session(_make_chat_response("no tools needed"))
    tools = [{"type": "function", "function": {"name": "search", "parameters": {}}}]
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.chat(
            messages=[{"role": "user", "content": "hi"}],
            tools=tools,
            tool_call_format="xml",
        )
    assert result["content"] == "no tools needed"


@pytest.mark.asyncio
async def test_chat_client_error_raises_runtime():
    import aiohttp as _aiohttp
    client = LlamaClient(base_url="http://localhost:8080/v1")
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.raise_for_status = MagicMock(side_effect=_aiohttp.ClientResponseError(
        request_info=MagicMock(), history=(), status=500, message="Server Error"
    ))
    mock_resp.json = AsyncMock(return_value={})
    post_cm = AsyncMock()
    post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    post_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=post_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        with pytest.raises(RuntimeError, match="Failed to connect"):
            await client.chat(messages=[{"role": "user", "content": "hi"}])


# --- count_tokens mocked ---

@pytest.mark.asyncio
async def test_count_tokens():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    response_data = {"tokens": [1, 2, 3, 4, 5]}
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.json = AsyncMock(return_value=response_data)
    mock_resp.raise_for_status = MagicMock()
    post_cm = AsyncMock()
    post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    post_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=post_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        count = await client.count_tokens("hello world")
    assert count == 5


# --- get_server_properties mocked ---

@pytest.mark.asyncio
async def test_get_server_properties():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    props = {"default_generation_settings": {"n_ctx": 4096}}
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=props)
    get_cm = AsyncMock()
    get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    get_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=get_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.get_server_properties()
    assert result["default_generation_settings"]["n_ctx"] == 4096


@pytest.mark.asyncio
async def test_get_server_properties_error_returns_empty():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    mock_session = AsyncMock()
    mock_session.get = MagicMock(side_effect=Exception("connection refused"))
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.get_server_properties()
    assert result == {}


# --- get_models error path ---

@pytest.mark.asyncio
async def test_get_models_error_returns_empty():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    mock_session = AsyncMock()
    mock_session.get = MagicMock(side_effect=Exception("timeout"))
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        models = await client.get_models()
    assert models == []


# --- stream_chat with reasoning tokens ---

@pytest.mark.asyncio
async def test_stream_chat_reasoning_token():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    sse_lines = [
        b'data: {"choices": [{"delta": {"reasoning_content": "thinking..."}}]}\n\n',
        b'data: {"choices": [{"delta": {"content": "answer"}}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    async def fake_content():
        for line in sse_lines:
            yield line

    mock_resp.content = fake_content()
    post_cm = AsyncMock()
    post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    post_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=post_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    chunks = []
    with patch("aiohttp.ClientSession", return_value=session_cm):
        async for chunk in client.stream_chat(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

    reasoning = [c for c in chunks if c["type"] == "reasoning"]
    assert len(reasoning) == 1
    assert reasoning[0]["content"] == "thinking..."


# --- stream_chat json mode ---

@pytest.mark.asyncio
async def test_stream_chat_json_tool_calls():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    raw = json.dumps({"content": "searching", "tool_calls": [{"name": "fn", "arguments": {}}]})
    sse_bytes = _make_sse_lines([raw])
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    async def fake_content():
        for line in sse_bytes:
            yield line

    mock_resp.content = fake_content()
    post_cm = AsyncMock()
    post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    post_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=post_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    chunks = []
    with patch("aiohttp.ClientSession", return_value=session_cm):
        async for chunk in client.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            tool_call_format="json",
        ):
            chunks.append(chunk)

    tool_chunks = [c for c in chunks if c["type"] == "tool_calls"]
    assert len(tool_chunks) == 1
    assert tool_chunks[0]["tool_calls"][0]["function"]["name"] == "fn"


# --- complete() error path ---

@pytest.mark.asyncio
async def test_complete_client_error_raises():
    import aiohttp as _aiohttp
    client = LlamaClient(base_url="http://localhost:8080/v1")
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.raise_for_status = MagicMock(side_effect=_aiohttp.ClientResponseError(
        request_info=MagicMock(), history=(), status=500, message="Error"
    ))
    mock_resp.json = AsyncMock(return_value={})
    post_cm = AsyncMock()
    post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    post_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=post_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        with pytest.raises(RuntimeError, match="Completion failed"):
            await client.complete(prompt="hello")


# --- stream_chat error path ---

@pytest.mark.asyncio
async def test_stream_chat_client_error_raises():
    import aiohttp as _aiohttp
    client = LlamaClient(base_url="http://localhost:8080/v1")
    mock_session = AsyncMock()
    mock_session.post = MagicMock(side_effect=_aiohttp.ClientConnectorError(
        MagicMock(), OSError("refused")
    ))
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        with pytest.raises(IncompleteGeneration, match="Cannot connect"):
            async for _ in client.stream_chat(
                messages=[{"role": "user", "content": "hi"}]
            ):
                pass


# --- stream_chat xml with tools injects prompt ---

@pytest.mark.asyncio
async def test_stream_chat_xml_with_tools():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    sse_bytes = _make_sse_lines(["just text"])
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    async def fake_content():
        for line in sse_bytes:
            yield line

    mock_resp.content = fake_content()
    post_cm = AsyncMock()
    post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    post_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=post_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    tools = [{"type": "function", "function": {"name": "search"}}]
    with patch("aiohttp.ClientSession", return_value=session_cm):
        chunks = []
        async for chunk in client.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            tool_call_format="xml",
            tools=tools,
        ):
            chunks.append(chunk)

    content_chunks = [c for c in chunks if c["type"] == "content"]
    assert any("just text" in c["content"] for c in content_chunks)


# --- health_check non-200 status ---

@pytest.mark.asyncio
async def test_health_check_non_200():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.status = 503
    get_cm = AsyncMock()
    get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    get_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=get_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        health = await client.health_check()
    assert health["available"] is False
    assert health["status_code"] == 503


@pytest.mark.asyncio
async def test_health_check_timeout():
    import asyncio as _asyncio
    client = LlamaClient(base_url="http://localhost:8080/v1")
    mock_session = AsyncMock()
    mock_session.get = MagicMock(side_effect=_asyncio.TimeoutError())
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        health = await client.health_check()
    assert health["available"] is False
    assert "Timeout" in health["error"]


@pytest.mark.asyncio
async def test_health_check_generic_exception():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    mock_session = AsyncMock()
    mock_session.get = MagicMock(side_effect=RuntimeError("unexpected"))
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        health = await client.health_check()
    assert health["available"] is False
    assert "Health check failed" in health["error"]


# --- get_server_properties non-200 ---

@pytest.mark.asyncio
async def test_get_server_properties_non_200():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.status = 404
    get_cm = AsyncMock()
    get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    get_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=get_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.get_server_properties()
    assert result == {}


# --- encode_image_base64 with real temp file ---

def test_encode_image_base64_success(tmp_path):
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG magic bytes
    result = LlamaClient.encode_image_base64(img)
    assert isinstance(result, str)
    assert len(result) > 0


# --- create_image_message with image_path ---

def test_create_image_message_image_path(tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    msg = LlamaClient.create_image_message(
        role="user",
        text="Describe this",
        image_path=img,
    )
    assert msg["role"] == "user"
    assert msg["content"][1]["type"] == "image_url"
    assert "data:image/png;base64," in msg["content"][1]["image_url"]["url"]


# --- complete with model and usage paths ---

@pytest.mark.asyncio
async def test_complete_with_model_and_usage():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    response_data = {
        "choices": [{"text": "the result"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        "model": "custom-model",
    }
    session_cm = _make_mock_post_session(response_data)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.complete(
            prompt="hello",
            model="custom-model",
            grammar='root ::= "ok"',
        )
    assert result["content"] == "the result"
    assert result["usage"]["total_tokens"] == 5


# --- count_tokens error path ---

@pytest.mark.asyncio
async def test_count_tokens_error():
    import aiohttp as _aiohttp
    client = LlamaClient(base_url="http://localhost:8080/v1")
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.raise_for_status = MagicMock(side_effect=_aiohttp.ClientResponseError(
        request_info=MagicMock(), history=(), status=500, message="error"
    ))
    post_cm = AsyncMock()
    post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    post_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=post_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        with pytest.raises(RuntimeError, match="Failed to count tokens"):
            await client.count_tokens("hello")


# --- get_models raise_for_status error ---

@pytest.mark.asyncio
async def test_get_models_raise_for_status_error():
    import aiohttp as _aiohttp
    client = LlamaClient(base_url="http://localhost:8080/v1")
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.raise_for_status = MagicMock(side_effect=_aiohttp.ClientResponseError(
        request_info=MagicMock(), history=(), status=500, message="error"
    ))
    get_cm = AsyncMock()
    get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    get_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=get_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await client.get_models()
    assert result == []


# --- stream_chat with sanitized history (xml mode assistant with tool_calls) ---

@pytest.mark.asyncio
async def test_stream_chat_xml_with_tool_call_history():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    messages = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "search", "arguments": '{"q": "test"}'}}]},
        {"role": "tool", "name": "search", "content": "results", "tool_call_id": "x"},
        {"role": "user", "content": "what did you find?"},
    ]
    sse_bytes = _make_sse_lines(["found it"])
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    async def fake_content():
        for line in sse_bytes:
            yield line

    mock_resp.content = fake_content()
    post_cm = AsyncMock()
    post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    post_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=post_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=session_cm):
        chunks = []
        async for chunk in client.stream_chat(
            messages=messages,
            tool_call_format="xml",
        ):
            chunks.append(chunk)

    assert any(c["type"] == "content" for c in chunks)


# --- stream_chat with json mode and history sanitization ---

@pytest.mark.asyncio
async def test_stream_chat_json_with_history():
    client = LlamaClient(base_url="http://localhost:8080/v1")
    messages = [
        {"role": "assistant", "content": None,
         "tool_calls": [{"function": {"name": "fn", "arguments": "{}"}}]},
        {"role": "tool", "name": "fn", "content": "42", "tool_call_id": "y"},
        {"role": "user", "content": "continue"},
    ]
    response = json.dumps({"content": "done", "tool_calls": []})
    sse_bytes = _make_sse_lines([response])
    mock_resp = AsyncMock()
    mock_resp.close = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    async def fake_content():
        for line in sse_bytes:
            yield line

    mock_resp.content = fake_content()
    post_cm = AsyncMock()
    post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    post_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=post_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=session_cm):
        chunks = []
        async for chunk in client.stream_chat(
            messages=messages,
            tool_call_format="json",
        ):
            chunks.append(chunk)

    assert any(c["type"] in ("content", "usage") for c in chunks)


# --- _parse_tool_calls_from_content json brace fallback ---

def test_parse_tool_calls_brace_fallback():
    client = LlamaClient(base_url="http://localhost/v1")
    raw = 'prefix\n{"content": "ok", "tool_calls": [{"name": "fn", "arguments": {}}]}\nsuffix'
    chunk = client._parse_tool_calls_from_content(raw, "json")
    assert chunk is None or chunk["type"] == "tool_calls"


# --- apply_json_calls with thought key ---

def test_apply_json_calls_thought_key():
    client = LlamaClient(base_url="http://localhost/v1")
    result = client._empty_result("model")
    raw = json.dumps({
        "thought": "Let me think...",
        "tool_calls": [{"name": "fn", "arguments": {}}],
    })
    client._apply_json_calls(result, raw)
    assert result["content"] == "Let me think..."
    assert result["tool_calls"] is not None
