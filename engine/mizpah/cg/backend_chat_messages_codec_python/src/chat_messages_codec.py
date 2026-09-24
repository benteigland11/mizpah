"""Chat Completions on one side, the Anthropic Messages API on the other.

A client that already speaks Chat Completions talks to a Messages-only
endpoint through two pure functions:

* ``chat_to_messages`` rewrites the request. System messages are hoisted
  into ``system`` (one block, optionally a cache breakpoint; the last tool and
  the newest message block carry the other two); assistant
  tool calls become ``tool_use`` blocks and tool results become
  ``tool_result`` blocks in a user turn; function tools become
  ``input_schema`` tools; ``reasoning_effort`` becomes
  ``output_config.effort``; ``max_tokens`` is required there, so a missing
  or non-positive one gets a caller-chosen default.
* ``messages_to_chat`` / ``fold_sse`` turn a Messages response, or its
  event stream read to the end, into one Chat-Completions-shaped response.

Nothing here does I/O.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

DEFAULT_MAX_TOKENS = 8192
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
STOP_TO_FINISH = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length",
                  "refusal": "content_filter", "pause_turn": "stop"}
DROPPED_FIELDS = ("frequency_penalty", "presence_penalty", "seed", "logit_bias", "n", "user", "store",
                  "response_format", "top_k", "min_p", "reasoning_format", "reasoning_budget_tokens", "chat_template_kwargs")


@dataclass(frozen=True)
class MessagesRequest:
    body: dict[str, Any]
    dropped_fields: tuple[str, ...] = ()


class CodecError(ValueError):
    """The input cannot be expressed on the other side."""


# --- request -----------------------------------------------------------------


def chat_to_messages(payload: dict[str, Any], *, default_max_tokens: int = DEFAULT_MAX_TOKENS, stream: bool = True,
                     cache_system: bool = True, cache_last_tool: bool = True,
                     cache_history: bool = True) -> MessagesRequest:
    """Rewrite a Chat Completions request body as a Messages request body."""
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise CodecError("messages must be a nonempty list")
    body: dict[str, Any] = {"stream": stream}
    if payload.get("model") is not None:
        body["model"] = payload["model"]
    system_parts: list[str] = []
    turns: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role in ("system", "developer"):
            text = _text_of(message.get("content"))
            if text:
                system_parts.append(text)
            continue
        turns.extend(_turns_for(message))
    if system_parts:
        block: dict[str, Any] = {"type": "text", "text": "\n".join(system_parts)}
        if cache_system:
            block["cache_control"] = {"type": "ephemeral"}
        body["system"] = [block]
    body["messages"] = _merge_adjacent(turns)
    if not body["messages"]:
        raise CodecError("no user or assistant messages to send")
    if cache_history:
        # A rolling breakpoint on the newest block: the next request finds this one's prefix within Anthropic's
        # 20-block lookback, so a growing conversation is read from cache rather than billed in full each turn.
        # Without it only system and tools were cached and every turn paid for the whole history.
        content = body["messages"][-1].get("content")
        if isinstance(content, str):
            body["messages"][-1]["content"] = content = [{"type": "text", "text": content}] if content else []
        if content:
            content[-1] = dict(content[-1], cache_control={"type": "ephemeral"})
    limit = payload.get("max_completion_tokens", payload.get("max_tokens"))
    body["max_tokens"] = int(limit) if isinstance(limit, (int, float)) and limit > 0 else default_max_tokens
    if payload.get("tools"):
        body["tools"] = [_tool(tool) for tool in payload["tools"]]
        if cache_last_tool:
            body["tools"][-1]["cache_control"] = {"type": "ephemeral"}
    if payload.get("tool_choice") is not None:
        body["tool_choice"] = _tool_choice(payload["tool_choice"], payload.get("parallel_tool_calls"))
    elif payload.get("parallel_tool_calls") is False and payload.get("tools"):
        body["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
    for key in ("temperature", "top_p", "metadata"):
        if payload.get(key) is not None:
            body[key] = payload[key]
    if payload.get("stop"):
        stop = payload["stop"]
        body["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
    effort = payload.get("reasoning_effort")
    if effort is not None:
        if effort not in EFFORT_LEVELS:
            raise CodecError(f"reasoning_effort must be one of {EFFORT_LEVELS}")
        body["output_config"] = dict(payload.get("output_config") or {}, effort=effort)
    if payload.get("thinking") is not None:
        body["thinking"] = payload["thinking"]
    dropped = tuple(key for key in payload if key in DROPPED_FIELDS)
    return MessagesRequest(body, dropped)


def _text_of(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    raise CodecError("message content must be a string or a list of parts")


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks: list[dict[str, Any]] = []
    for part in content or []:
        if not isinstance(part, dict):
            raise CodecError("content parts must be objects")
        kind = part.get("type")
        if kind == "text":
            if part.get("text"):
                blocks.append({"type": "text", "text": part["text"]})
        elif kind == "image_url":
            image = part.get("image_url") or {}
            url = image.get("url") if isinstance(image, dict) else image
            blocks.append(_image_block(str(url or "")))
        else:
            raise CodecError(f"unsupported content part {kind!r}")
    return blocks


def _image_block(url: str) -> dict[str, Any]:
    if url.startswith("data:"):
        header, _, data = url.partition(",")
        media = header[5:].split(";", 1)[0] or "image/png"
        return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
    return {"type": "image", "source": {"type": "url", "url": url}}


def _turns_for(message: dict[str, Any]) -> list[dict[str, Any]]:
    role = message.get("role")
    if role == "tool":
        call_id = message.get("tool_call_id")
        if not call_id:
            raise CodecError("tool messages need tool_call_id")
        return [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id,
                                             "content": _text_of(message.get("content"))}]}]
    if role == "assistant":
        blocks = _blocks(message.get("content"))
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except ValueError as error:
                    raise CodecError(f"tool call arguments are not JSON: {error}") from error
            blocks.append({"type": "tool_use", "id": call.get("id", ""), "name": function.get("name", ""), "input": arguments})
        return [{"role": "assistant", "content": blocks}] if blocks else []
    if role == "user":
        blocks = _blocks(message.get("content") or "")
        return [{"role": "user", "content": blocks}] if blocks else []
    raise CodecError(f"unsupported role {role!r}")


def _merge_adjacent(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages wants strict alternation; consecutive same-role turns (several tool results) merge."""
    merged: list[dict[str, Any]] = []
    for turn in turns:
        if merged and merged[-1]["role"] == turn["role"]:
            merged[-1]["content"] = merged[-1]["content"] + turn["content"]
        else:
            merged.append({"role": turn["role"], "content": list(turn["content"])})
    return merged


def _tool(tool: dict[str, Any]) -> dict[str, Any]:
    if tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
        raise CodecError("only function tools translate")
    function = tool["function"]
    return {"name": function["name"], "description": function.get("description", ""),
            "input_schema": function.get("parameters") or {"type": "object", "properties": {}}}


def _tool_choice(choice: Any, parallel: Any) -> dict[str, Any]:
    if isinstance(choice, dict) and choice.get("type") == "function":
        result: dict[str, Any] = {"type": "tool", "name": (choice.get("function") or {}).get("name", "")}
    elif choice == "required":
        result = {"type": "any"}
    elif choice == "none":
        result = {"type": "none"}
    else:
        result = {"type": "auto"}
    if parallel is False and result["type"] in ("auto", "any"):
        result["disable_parallel_tool_use"] = True
    return result


# --- response ----------------------------------------------------------------


def messages_to_chat(message: dict[str, Any]) -> dict[str, Any]:
    """One Messages object -> one Chat Completions object with a single choice."""
    texts: list[str] = []
    thinking: list[str] = []
    calls: list[dict[str, Any]] = []
    for block in message.get("content") or []:
        kind = block.get("type")
        if kind == "text":
            texts.append(block.get("text", ""))
        elif kind == "tool_use":
            calls.append({"id": block.get("id", ""), "type": "function",
                          "function": {"name": block.get("name", ""),
                                       "arguments": json.dumps(block.get("input") or {}, separators=(",", ":"))}})
        elif kind in ("thinking", "redacted_thinking"):
            if block.get("thinking"):
                thinking.append(block["thinking"])
    reply: dict[str, Any] = {"role": "assistant", "content": "".join(texts) if texts else None}
    if thinking:
        reply["reasoning_content"] = "\n".join(thinking)
    if calls:
        reply["tool_calls"] = calls
    stop = message.get("stop_reason")
    finish = "tool_calls" if calls and stop in (None, "tool_use", "end_turn") else STOP_TO_FINISH.get(stop or "end_turn", "stop")
    result: dict[str, Any] = {"id": message.get("id"), "object": "chat.completion", "model": message.get("model"),
                              "choices": [{"index": 0, "message": reply, "finish_reason": finish}],
                              "usage": _usage(message.get("usage") or {})}
    if message.get("type") == "error" or message.get("error"):
        error = message.get("error") or {}
        result["error"] = {"code": error.get("type"), "message": error.get("message")}
        result["choices"][0]["finish_reason"] = "error"
    return result


def _usage(usage: dict[str, Any]) -> dict[str, Any]:
    fresh = int(usage.get("input_tokens") or 0)
    cached = int(usage.get("cache_read_input_tokens") or 0)
    created = int(usage.get("cache_creation_input_tokens") or 0)
    completion = int(usage.get("output_tokens") or 0)
    prompt = fresh + cached + created   # Anthropic counts cached prefix separately; Chat wants the whole prompt
    result = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
              "prompt_tokens_details": {"cached_tokens": cached}}
    if created:
        result["prompt_tokens_details"]["cache_creation_tokens"] = created
    return result


@dataclass
class StreamState:
    message: dict[str, Any] = field(default_factory=dict)
    blocks: dict[int, dict[str, Any]] = field(default_factory=dict)
    partial_json: dict[int, list[str]] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    ended: bool = False


def sse_events(lines: Iterable[str]) -> Iterable[dict[str, Any]]:
    """Yield the JSON of each ``data:`` block in a text/event-stream."""
    buffer: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            if buffer:
                data = "\n".join(buffer)
                buffer = []
                yield json.loads(data)
            continue
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
    if buffer:
        yield json.loads("\n".join(buffer))


def feed(state: StreamState, event: dict[str, Any]) -> None:
    kind = event.get("type")
    index = event.get("index")
    if kind == "message_start":
        state.message = dict(event.get("message") or {})
        state.message["content"] = []
    elif kind == "content_block_start" and index is not None:
        state.blocks[index] = dict(event.get("content_block") or {})
        if state.blocks[index].get("type") == "tool_use":
            state.partial_json[index] = []
    elif kind == "content_block_delta" and index is not None:
        delta = event.get("delta") or {}
        block = state.blocks.setdefault(index, {"type": "text", "text": ""})
        if delta.get("type") == "text_delta":
            block["text"] = block.get("text", "") + delta.get("text", "")
        elif delta.get("type") == "input_json_delta":
            state.partial_json.setdefault(index, []).append(delta.get("partial_json", ""))
        elif delta.get("type") == "thinking_delta":
            block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
    elif kind == "content_block_stop" and index is not None:
        block = state.blocks.get(index)
        if block is not None and block.get("type") == "tool_use":
            raw = "".join(state.partial_json.get(index, []))
            try:
                block["input"] = json.loads(raw) if raw.strip() else {}
            except ValueError:
                block["input"] = {"_raw": raw}
    elif kind == "message_delta":
        delta = event.get("delta") or {}
        if delta.get("stop_reason"):
            state.message["stop_reason"] = delta["stop_reason"]
        usage = event.get("usage") or {}
        merged = dict(state.message.get("usage") or {})
        merged.update({k: v for k, v in usage.items() if v is not None})
        state.message["usage"] = merged
    elif kind == "message_stop":
        state.ended = True
    elif kind == "error":
        state.error = event.get("error") or {"message": "stream error"}
        state.ended = True


def fold_sse(lines: Iterable[str]) -> dict[str, Any]:
    """Read a Messages SSE stream to its end; return one Chat Completions object."""
    state = StreamState()
    for event in sse_events(lines):
        feed(state, event)
        if state.ended:
            break
    message = dict(state.message)
    message["content"] = [state.blocks[i] for i in sorted(state.blocks)]
    if state.error:
        message["error"] = state.error
    elif not state.ended:
        message.setdefault("stop_reason", "max_tokens")   # cut short: the caller sees length, never a fake stop
    return messages_to_chat(message)
