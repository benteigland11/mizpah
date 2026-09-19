import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.chat_responses_codec import (  # noqa: E402
    CodecError,
    StreamState,
    chat_to_responses,
    feed,
    fold_sse,
    responses_to_chat,
    sse_events,
)

CHAT_REQUEST = {
    "model": "model-x",
    "messages": [
        {"role": "system", "content": "Be terse."},
        {"role": "user", "content": [{"type": "text", "text": "Look:"},
                                     {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "low"}}]},
        {"role": "assistant", "content": "Checking.", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": {"q": "x"}}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "found"},
        {"role": "user", "content": "Thanks"},
    ],
    "tools": [{"type": "function", "function": {"name": "lookup", "description": "Find", "parameters": {"type": "object"}}}],
    "tool_choice": "auto",
    "max_tokens": 50,
    "temperature": 0.2,
    "top_k": 40,
    "seed": 7,
}


def test_request_translation() -> None:
    result = chat_to_responses(CHAT_REQUEST)
    body = result.body
    assert body["instructions"] == "Be terse."
    assert body["stream"] is True and body["store"] is False and body["model"] == "model-x"
    assert body["max_output_tokens"] == 50 and body["temperature"] == 0.2
    assert result.dropped_fields == ("top_k", "seed")
    kinds = [(item["type"], item.get("role")) for item in body["input"]]
    assert kinds == [("message", "user"), ("message", "assistant"), ("function_call", None),
                     ("function_call_output", None), ("message", "user")]
    user = body["input"][0]["content"]
    assert user == [{"type": "input_text", "text": "Look:"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "low"}]
    assert body["input"][1]["content"] == [{"type": "output_text", "text": "Checking."}]
    assert body["input"][2] == {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": '{"q": "x"}'}
    assert body["input"][3] == {"type": "function_call_output", "call_id": "call_1", "output": "found"}
    assert body["tools"] == [{"type": "function", "name": "lookup", "description": "Find", "parameters": {"type": "object"},
                              "strict": False}]


def test_request_options() -> None:
    body = chat_to_responses({"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                              "tool_choice": {"type": "function", "function": {"name": "f"}},
                              "response_format": {"type": "json_schema", "json_schema": {"name": "n", "schema": {"type": "object"}}}},
                             system_as_instructions=False, stream=False, store=True).body
    assert "instructions" not in body and body["input"][0]["role"] == "system"
    assert body["tool_choice"] == {"type": "function", "name": "f"}
    assert body["text"] == {"format": {"type": "json_schema", "name": "n", "schema": {"type": "object"}, "strict": False}}
    assert body["stream"] is False and body["store"] is True
    assert chat_to_responses({"messages": [{"role": "user", "content": "u"}], "response_format": {"type": "json_object"}}).body["text"] == {"format": {"type": "json_object"}}


def test_request_rejections() -> None:
    with pytest.raises(CodecError):
        chat_to_responses({"messages": []})
    with pytest.raises(CodecError):
        chat_to_responses({"messages": [{"role": "tool", "content": "x"}]})
    with pytest.raises(CodecError):
        chat_to_responses({"messages": [{"role": "user", "content": [{"type": "audio"}]}]})
    with pytest.raises(CodecError):
        chat_to_responses({"messages": [{"role": "user", "content": "u"}], "tools": [{"type": "web_search"}]})
    with pytest.raises(CodecError):
        chat_to_responses({"messages": [{"role": "narrator", "content": "u"}]})


COMPLETED = {
    "id": "resp_1", "model": "model-x", "status": "completed",
    "output": [
        {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "think"}]},
        {"type": "message", "id": "msg_1", "role": "assistant", "content": [{"type": "output_text", "text": "Hello "},
                                                                           {"type": "output_text", "text": "there"}]},
        {"type": "function_call", "id": "fc_1", "call_id": "call_9", "name": "lookup", "arguments": "{\"q\":1}"},
    ],
    "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14,
              "input_tokens_details": {"cached_tokens": 3}, "output_tokens_details": {"reasoning_tokens": 2}},
}


def test_response_translation() -> None:
    chat = responses_to_chat(COMPLETED)
    assert chat["id"] == "resp_1" and chat["model"] == "model-x" and chat["object"] == "chat.completion"
    choice = chat["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"] == {"role": "assistant", "content": "Hello there", "reasoning_content": "think",
                                 "tool_calls": [{"id": "call_9", "type": "function",
                                                 "function": {"name": "lookup", "arguments": "{\"q\":1}"}}]}
    assert chat["usage"] == {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14,
                             "prompt_tokens_details": {"cached_tokens": 3}, "completion_tokens_details": {"reasoning_tokens": 2}}


def test_response_finish_reasons() -> None:
    assert responses_to_chat({"output": [], "status": "completed"})["choices"][0]["finish_reason"] == "stop"
    assert responses_to_chat({"output": [], "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}})["choices"][0]["finish_reason"] == "length"
    assert responses_to_chat({"output": [], "status": "incomplete", "incomplete_details": {"reason": "content_filter"}})["choices"][0]["finish_reason"] == "content_filter"
    failed = responses_to_chat({"output": [], "status": "failed", "error": {"code": "x", "message": "m"}})
    assert failed["choices"][0]["finish_reason"] == "error" and failed["error"]["code"] == "x"
    assert failed["choices"][0]["message"]["content"] is None
    assert failed["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _sse(events: list[dict]) -> list[str]:
    lines: list[str] = []
    for event in events:
        lines += [f"event: {event['type']}\n", f"data: {json.dumps(event)}\n", "\n"]
    return lines


def test_fold_sse_uses_terminal_response() -> None:
    lines = _sse([
        {"type": "response.created", "response": {"id": "resp_1"}},
        {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": "m"}},
        {"type": "response.output_text.delta", "output_index": 0, "delta": "partial"},
        {"type": "response.completed", "response": COMPLETED},
        {"type": "response.output_text.delta", "output_index": 0, "delta": "never read"},
    ])
    chat = fold_sse(lines)
    assert chat["choices"][0]["message"]["content"] == "Hello there"
    assert chat["usage"]["prompt_tokens"] == 10


def test_fold_sse_synthesizes_when_cut_short() -> None:
    lines = _sse([
        {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": "m", "role": "assistant"}},
        {"type": "response.output_text.delta", "output_index": 0, "delta": "Hel"},
        {"type": "response.output_text.delta", "output_index": 0, "delta": "lo"},
        {"type": "response.output_item.added", "output_index": 1, "item": {"type": "function_call", "call_id": "c1", "name": "f"}},
        {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": "{\"a\":"},
        {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": "1}"},
        {"type": "response.output_item.added", "output_index": 2, "item": {"type": "reasoning"}},
        {"type": "response.reasoning_summary_text.delta", "output_index": 2, "delta": "why"},
    ])
    chat = fold_sse(lines)
    message = chat["choices"][0]["message"]
    assert message["content"] == "Hello" and message["reasoning_content"] == "why"
    assert message["tool_calls"][0]["function"] == {"name": "f", "arguments": "{\"a\":1}"}
    assert chat["choices"][0]["finish_reason"] == "length"
    assert chat["usage"]["prompt_tokens"] == 0


def test_fold_sse_error_event() -> None:
    chat = fold_sse(_sse([{"type": "error", "code": "rate_limited", "message": "slow"}]))
    assert chat["choices"][0]["finish_reason"] == "error" and chat["error"]["code"] == "rate_limited"


def test_sse_parser_handles_multiline_done_and_missing_trailing_blank() -> None:
    lines = ["data: {\"a\":\n", "data: 1}\n", "\n", ": comment\n", "data: [DONE]\n", "\n", "data: {\"b\": 2}"]
    assert list(sse_events(lines)) == [{"a": 1}, {"b": 2}]


def test_feed_output_item_done_overrides_added() -> None:
    state = StreamState()
    feed(state, {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message"}})
    feed(state, {"type": "response.output_item.done", "output_index": 0,
                 "item": {"type": "message", "content": [{"type": "output_text", "text": "final"}]}})
    assert state.items[0]["content"][0]["text"] == "final"
    assert not state.ended
