"""Chat Completions on one side, the Responses API on the other.

A client that already speaks Chat Completions (``messages``, ``tools``,
``choices[0].message``) can talk to a Responses-only endpoint through two
pure functions:

* ``chat_to_responses`` rewrites the request. System text becomes
  ``instructions``; messages become typed ``input`` items; tool calls and
  tool results become ``function_call`` / ``function_call_output`` items;
  ``reasoning_effort`` becomes ``reasoning.effort``; Chat-only sampling
  fields are dropped and reported, never silently kept.
* ``fold_sse`` reads a Responses event stream to its end and returns one
  Chat-Completions-shaped response. The terminal ``response.completed`` (or
  ``incomplete`` / ``failed``) object is authoritative; deltas are only
  kept as a fallback for a stream that was cut before it.

Nothing here does I/O. The caller owns the socket and the byte limit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

PASSTHROUGH_FIELDS = ("temperature", "top_p", "metadata", "parallel_tool_calls", "reasoning", "include", "user")
RENAMED_FIELDS = {"max_tokens": "max_output_tokens", "max_completion_tokens": "max_output_tokens"}
FINISH_BY_STATUS = {"completed": "stop", "incomplete": "length", "failed": "error", "cancelled": "error"}


@dataclass(frozen=True)
class ResponsesRequest:
    body: dict[str, Any]
    dropped_fields: tuple[str, ...] = ()


@dataclass
class StreamState:
    """Accumulated view of a stream, used only if no terminal response arrives."""

    response: dict[str, Any] | None = None
    items: dict[int, dict[str, Any]] = field(default_factory=dict)
    text: dict[int, list[str]] = field(default_factory=dict)
    arguments: dict[int, list[str]] = field(default_factory=dict)
    reasoning: dict[int, list[str]] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    ended: bool = False


class CodecError(ValueError):
    """The input cannot be expressed on the other side."""


# --- request -----------------------------------------------------------------


def chat_to_responses(payload: dict[str, Any], *, store: bool = False, stream: bool = True,
                      system_as_instructions: bool = True, strict_tools: bool = False) -> ResponsesRequest:
    """Rewrite a Chat Completions request body as a Responses request body."""
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise CodecError("messages must be a nonempty list")
    body: dict[str, Any] = {"stream": stream, "store": store}
    if payload.get("model") is not None:
        body["model"] = payload["model"]
    instructions: list[str] = []
    body["input"] = []
    for message in messages:
        role = message.get("role")
        if role in ("system", "developer") and system_as_instructions:
            instructions.append(_text_of(message.get("content")))
            continue
        body["input"].extend(_message_items(message))
    if instructions:
        body["instructions"] = "\n\n".join(part for part in instructions if part)
    if payload.get("tools"):
        body["tools"] = [_tool_item(tool, strict_tools) for tool in payload["tools"]]
    if payload.get("tool_choice") is not None:
        body["tool_choice"] = _tool_choice(payload["tool_choice"])
    if payload.get("response_format") is not None:
        body["text"] = {"format": _text_format(payload["response_format"])}
    dropped = []
    if payload.get("reasoning_effort") is not None:
        reasoning = dict(payload.get("reasoning") or {})
        reasoning.setdefault("effort", payload["reasoning_effort"])
        body["reasoning"] = reasoning
    for key, value in payload.items():
        if key in ("messages", "model", "tools", "tool_choice", "response_format", "stream", "store", "reasoning_effort"):
            continue
        if key in RENAMED_FIELDS:
            if RENAMED_FIELDS[key] == "max_output_tokens" and isinstance(value, (int, float)) and value <= 0:
                continue   # llama.cpp's -1 means unlimited; the Responses API has no such value, so the limit is omitted
            body[RENAMED_FIELDS[key]] = value
        elif key in PASSTHROUGH_FIELDS:
            body[key] = value
        else:
            dropped.append(key)
    return ResponsesRequest(body, tuple(dropped))


def _text_of(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    raise CodecError("message content must be a string or a list of parts")


def _content_parts(content: Any, *, output: bool) -> list[dict[str, Any]]:
    text_type = "output_text" if output else "input_text"
    if isinstance(content, str):
        return [{"type": text_type, "text": content}]
    parts: list[dict[str, Any]] = []
    for part in content or []:
        if not isinstance(part, dict):
            raise CodecError("content parts must be objects")
        kind = part.get("type")
        if kind == "text":
            parts.append({"type": text_type, "text": part.get("text", "")})
        elif kind == "image_url" and not output:
            image = part.get("image_url") or {}
            url = image.get("url") if isinstance(image, dict) else image
            item: dict[str, Any] = {"type": "input_image", "image_url": url}
            if isinstance(image, dict) and image.get("detail"):
                item["detail"] = image["detail"]
            parts.append(item)
        else:
            raise CodecError(f"unsupported content part {kind!r}")
    return parts


def _message_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    role = message.get("role")
    if role == "tool":
        call_id = message.get("tool_call_id")
        if not call_id:
            raise CodecError("tool messages need tool_call_id")
        return [{"type": "function_call_output", "call_id": call_id, "output": _text_of(message.get("content"))}]
    if role == "assistant":
        items: list[dict[str, Any]] = []
        content = message.get("content")
        if content:
            items.append({"type": "message", "role": "assistant", "content": _content_parts(content, output=True)})
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            arguments = function.get("arguments", "")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments)
            items.append({"type": "function_call", "call_id": call.get("id", ""), "name": function.get("name", ""),
                          "arguments": arguments})
        return items
    if role in ("user", "system", "developer"):
        return [{"type": "message", "role": role, "content": _content_parts(message.get("content") or "", output=False)}]
    raise CodecError(f"unsupported role {role!r}")


def _tool_item(tool: dict[str, Any], strict: bool) -> dict[str, Any]:
    if tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
        raise CodecError("only function tools translate")
    function = tool["function"]
    return {"type": "function", "name": function["name"], "description": function.get("description", ""),
            "parameters": function.get("parameters") or {"type": "object", "properties": {}}, "strict": strict}


def _tool_choice(choice: Any) -> Any:
    if isinstance(choice, dict) and choice.get("type") == "function":
        return {"type": "function", "name": (choice.get("function") or {}).get("name", "")}
    return choice


def _text_format(response_format: Any) -> dict[str, Any]:
    if isinstance(response_format, dict) and response_format.get("type") == "json_schema":
        schema = response_format.get("json_schema") or {}
        return {"type": "json_schema", "name": schema.get("name", "response"), "schema": schema.get("schema") or {},
                "strict": bool(schema.get("strict", False))}
    if isinstance(response_format, dict):
        return {"type": response_format.get("type", "text")}
    return {"type": str(response_format)}


# --- response ----------------------------------------------------------------


def responses_to_chat(response: dict[str, Any]) -> dict[str, Any]:
    """One Responses object -> one Chat Completions object with a single choice."""
    texts: list[str] = []
    reasoning: list[str] = []
    calls: list[dict[str, Any]] = []
    for item in response.get("output") or []:
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    texts.append(part.get("refusal", ""))
        elif kind == "function_call":
            calls.append({"id": item.get("call_id") or item.get("id") or "", "type": "function",
                          "function": {"name": item.get("name", ""), "arguments": item.get("arguments", "")}})
        elif kind == "reasoning":
            for part in item.get("summary") or []:
                if part.get("text"):
                    reasoning.append(part["text"])
    message: dict[str, Any] = {"role": "assistant", "content": "".join(texts) if texts else None}
    if reasoning:
        message["reasoning_content"] = "\n".join(reasoning)
    if calls:
        message["tool_calls"] = calls
    status = response.get("status") or "completed"
    finish = "tool_calls" if calls and status == "completed" else FINISH_BY_STATUS.get(status, "stop")
    details = response.get("incomplete_details") or {}
    if status == "incomplete" and details.get("reason") == "content_filter":
        finish = "content_filter"
    result: dict[str, Any] = {"id": response.get("id"), "object": "chat.completion", "model": response.get("model"),
                              "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                              "usage": _usage(response.get("usage") or {})}
    if response.get("error"):
        result["error"] = response["error"]
    return result


def _usage(usage: dict[str, Any]) -> dict[str, Any]:
    prompt = int(usage.get("input_tokens") or 0)
    completion = int(usage.get("output_tokens") or 0)
    result = {"prompt_tokens": prompt, "completion_tokens": completion,
              "total_tokens": int(usage.get("total_tokens") or prompt + completion)}
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens")
    if cached is not None:
        result["prompt_tokens_details"] = {"cached_tokens": int(cached)}
    reasoning = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
    if reasoning is not None:
        result["completion_tokens_details"] = {"reasoning_tokens": int(reasoning)}
    return result


def sse_events(lines: Iterable[str]) -> Iterable[dict[str, Any]]:
    """Yield the JSON of each ``data:`` block in a text/event-stream."""
    buffer: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            if buffer:
                data = "\n".join(buffer)
                buffer = []
                if data.strip() != "[DONE]":
                    yield json.loads(data)
            continue
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
    if buffer:
        data = "\n".join(buffer)
        if data.strip() != "[DONE]":
            yield json.loads(data)


def feed(state: StreamState, event: dict[str, Any]) -> None:
    """Apply one Responses stream event to the state."""
    kind = event.get("type", "")
    index = event.get("output_index")
    if kind in ("response.completed", "response.incomplete", "response.failed", "response.cancelled"):
        state.response = event.get("response") or {}
        state.ended = True
    elif kind == "error":
        state.error = {"code": event.get("code"), "message": event.get("message")}
        state.ended = True
    elif kind == "response.output_item.added" and index is not None:
        state.items[index] = dict(event.get("item") or {})
    elif kind == "response.output_item.done" and index is not None:
        state.items[index] = dict(event.get("item") or {})
    elif kind == "response.output_text.delta" and index is not None:
        state.text.setdefault(index, []).append(event.get("delta", ""))
    elif kind == "response.function_call_arguments.delta" and index is not None:
        state.arguments.setdefault(index, []).append(event.get("delta", ""))
    elif kind == "response.reasoning_summary_text.delta" and index is not None:
        state.reasoning.setdefault(index, []).append(event.get("delta", ""))


def synthesize_response(state: StreamState) -> dict[str, Any]:
    """Best-effort Responses object from deltas alone, for a stream cut short."""
    output: list[dict[str, Any]] = []
    for index in sorted(state.items):
        item = dict(state.items[index])
        if item.get("type") == "message" and not item.get("content"):
            item["content"] = [{"type": "output_text", "text": "".join(state.text.get(index, []))}]
        elif item.get("type") == "function_call" and not item.get("arguments"):
            item["arguments"] = "".join(state.arguments.get(index, []))
        elif item.get("type") == "reasoning" and not item.get("summary"):
            item["summary"] = [{"type": "summary_text", "text": "".join(state.reasoning.get(index, []))}]
        output.append(item)
    response: dict[str, Any] = {"output": output, "status": "failed" if state.error else "incomplete",
                                "incomplete_details": {"reason": "stream_ended"}}
    if state.error:
        response["error"] = state.error
    return response


def fold_sse(lines: Iterable[str]) -> dict[str, Any]:
    """Read a Responses SSE stream to its end; return one Chat Completions object."""
    state = StreamState()
    for event in sse_events(lines):
        feed(state, event)
        if state.ended:
            break
    if state.response is not None:
        response = dict(state.response)
        if not response.get("output") and state.items:
            # Some backends (ChatGPT's Codex backend) end the stream with a response.completed whose output is
            # empty; the items arrived in output_item.done and are the answer.
            response["output"] = synthesize_response(state)["output"]
        result = responses_to_chat(response)
        if state.error and "error" not in result:
            result["error"] = state.error
        return result
    return responses_to_chat(synthesize_response(state))
