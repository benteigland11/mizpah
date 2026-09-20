import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.chat_messages_codec import CodecError, chat_to_messages, fold_sse, messages_to_chat  # noqa: E402

CHAT = {
    "model": "model-x",
    "messages": [
        {"role": "system", "content": "Be terse."},
        {"role": "developer", "content": "And kind."},
        {"role": "user", "content": [{"type": "text", "text": "Look:"},
                                     {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]},
        {"role": "assistant", "content": "Checking.", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{\"q\": \"x\"}"}},
            {"id": "call_2", "type": "function", "function": {"name": "lookup", "arguments": {"q": "y"}}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "found x"},
        {"role": "tool", "tool_call_id": "call_2", "content": "found y"},
        {"role": "user", "content": "Thanks"},
    ],
    "tools": [{"type": "function", "function": {"name": "lookup", "description": "Find", "parameters": {"type": "object"}}}],
    "max_tokens": 50, "temperature": 0.2, "stop": ["END"], "seed": 7, "top_k": 4, "reasoning_effort": "high",
}


def test_request_translation() -> None:
    result = chat_to_messages(CHAT)
    body = result.body
    assert body["system"] == [{"type": "text", "text": "Be terse.\nAnd kind.", "cache_control": {"type": "ephemeral"}}]
    assert body["stream"] is True and body["max_tokens"] == 50 and body["temperature"] == 0.2 and body["stop_sequences"] == ["END"]
    assert body["output_config"] == {"effort": "high"}
    assert set(result.dropped_fields) == {"seed", "top_k"}
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user"]  # tool results and the next user text merge into one user turn
    assert body["messages"][0]["content"][1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
    assistant = body["messages"][1]["content"]
    assert assistant[0] == {"type": "text", "text": "Checking."}
    assert assistant[1] == {"type": "tool_use", "id": "call_1", "name": "lookup", "input": {"q": "x"}}
    assert assistant[2]["input"] == {"q": "y"}
    results = body["messages"][2]["content"]
    assert results == [{"type": "tool_result", "tool_use_id": "call_1", "content": "found x"},
                       {"type": "tool_result", "tool_use_id": "call_2", "content": "found y"},
                       {"type": "text", "text": "Thanks"}]
    assert body["tools"] == [{"name": "lookup", "description": "Find", "input_schema": {"type": "object"},
                              "cache_control": {"type": "ephemeral"}}]


def test_request_options_and_defaults() -> None:
    body = chat_to_messages({"messages": [{"role": "user", "content": "u"}], "max_tokens": -1,
                             "tool_choice": {"type": "function", "function": {"name": "f"}}, "parallel_tool_calls": False},
                            default_max_tokens=1234, stream=False, cache_system=False).body
    assert body["max_tokens"] == 1234 and body["stream"] is False
    assert body["tool_choice"] == {"type": "tool", "name": "f"}
    body = chat_to_messages({"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                             "tool_choice": "required", "parallel_tool_calls": False, "max_completion_tokens": 9}, cache_system=False).body
    assert body["system"] == [{"type": "text", "text": "s"}] and body["max_tokens"] == 9
    assert body["tool_choice"] == {"type": "any", "disable_parallel_tool_use": True}
    body = chat_to_messages({"messages": [{"role": "user", "content": "u"}], "tools": [{"type": "function", "function": {"name": "f"}}],
                             "tool_choice": "none"}, cache_last_tool=False).body
    assert body["tool_choice"] == {"type": "none"} and "cache_control" not in body["tools"][0]
    assert chat_to_messages({"messages": [{"role": "user", "content": "u"}], "thinking": {"type": "enabled", "budget_tokens": 10}}).body["thinking"]["budget_tokens"] == 10


def test_request_rejections() -> None:
    for bad in ({"messages": []}, {"messages": [{"role": "system", "content": "only system"}]},
                {"messages": [{"role": "tool", "content": "x"}]}, {"messages": [{"role": "user", "content": [{"type": "audio"}]}]},
                {"messages": [{"role": "user", "content": "u"}], "reasoning_effort": "ultra"},
                {"messages": [{"role": "user", "content": "u"}], "tools": [{"type": "web_search"}]},
                {"messages": [{"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "f", "arguments": "{bad"}}]}]}):
        with pytest.raises(CodecError):
            chat_to_messages(bad)


MESSAGE = {"id": "msg_1", "type": "message", "role": "assistant", "model": "model-x", "stop_reason": "tool_use",
           "content": [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "Hello "}, {"type": "text", "text": "there"},
                       {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"q": 1}}],
           "usage": {"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 5}}


def test_response_translation() -> None:
    chat = messages_to_chat(MESSAGE)
    assert chat["id"] == "msg_1" and chat["model"] == "model-x"
    choice = chat["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"] == {"role": "assistant", "content": "Hello there", "reasoning_content": "hmm",
                                 "tool_calls": [{"id": "toolu_1", "type": "function", "function": {"name": "lookup", "arguments": "{\"q\":1}"}}]}
    assert chat["usage"] == {"prompt_tokens": 45, "completion_tokens": 4, "total_tokens": 49,
                             "prompt_tokens_details": {"cached_tokens": 30, "cache_creation_tokens": 5}}


def test_response_finish_reasons_and_errors() -> None:
    assert messages_to_chat({"content": [{"type": "text", "text": "x"}], "stop_reason": "end_turn"})["choices"][0]["finish_reason"] == "stop"
    assert messages_to_chat({"content": [], "stop_reason": "max_tokens"})["choices"][0]["finish_reason"] == "length"
    assert messages_to_chat({"content": [], "stop_reason": "refusal"})["choices"][0]["finish_reason"] == "content_filter"
    failed = messages_to_chat({"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})
    assert failed["choices"][0]["finish_reason"] == "error" and failed["error"] == {"code": "overloaded_error", "message": "busy"}
    assert failed["usage"]["prompt_tokens"] == 0 and failed["choices"][0]["message"]["content"] is None


def _sse(events: list[dict]) -> list[str]:
    lines: list[str] = []
    for event in events:
        lines += [f"event: {event['type']}\n", f"data: {json.dumps(event)}\n", "\n"]
    return lines


def test_fold_sse_assembles_blocks() -> None:
    chat = fold_sse(_sse([
        {"type": "message_start", "message": {"id": "msg_2", "model": "model-x", "usage": {"input_tokens": 7, "cache_read_input_tokens": 3}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_9", "name": "f", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"a\":"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": " 1}"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 12}},
        {"type": "message_stop"},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "never read"}},
    ]))
    message = chat["choices"][0]["message"]
    assert message["content"] == "Hello" and message["tool_calls"][0]["function"] == {"name": "f", "arguments": "{\"a\":1}"}
    assert chat["choices"][0]["finish_reason"] == "tool_calls" and chat["id"] == "msg_2"
    assert chat["usage"] == {"prompt_tokens": 10, "completion_tokens": 12, "total_tokens": 22, "prompt_tokens_details": {"cached_tokens": 3}}


def test_fold_sse_cut_short_and_error() -> None:
    chat = fold_sse(_sse([{"type": "message_start", "message": {"id": "m"}},
                          {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                          {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "partial"}}]))
    assert chat["choices"][0]["message"]["content"] == "partial" and chat["choices"][0]["finish_reason"] == "length"
    chat = fold_sse(_sse([{"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}]))
    assert chat["choices"][0]["finish_reason"] == "error" and chat["error"]["message"] == "busy"
    chat = fold_sse(_sse([{"type": "message_start", "message": {"id": "m"}},
                          {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "t", "name": "f"}},
                          {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{oops"}},
                          {"type": "content_block_stop", "index": 0}, {"type": "message_stop"}]))
    assert json.loads(chat["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]) == {"_raw": "{oops"}
