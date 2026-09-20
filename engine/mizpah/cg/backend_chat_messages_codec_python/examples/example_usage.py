"""Translate a Chat Completions request to a Messages request, then fold a canned Messages stream back."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.chat_messages_codec import chat_to_messages, fold_sse

request = chat_to_messages({
    "model": "example-model",
    "messages": [{"role": "system", "content": "You answer in one line."},
                 {"role": "user", "content": "What is the capital of France?"}],
    "tools": [{"type": "function", "function": {"name": "lookup", "description": "Look a fact up",
                                                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}],
    "max_tokens": 64, "reasoning_effort": "low", "seed": 1,
})
print("Messages body:")
print(json.dumps(request.body, indent=2))
print("dropped Chat-only fields:", request.dropped_fields)

events = [
    {"type": "message_start", "message": {"id": "msg_demo", "model": "example-model", "usage": {"input_tokens": 21}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Paris."}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
    {"type": "message_stop"},
]
stream = []
for event in events:
    stream += [f"event: {event['type']}\n", f"data: {json.dumps(event)}\n", "\n"]
print("\nChat Completions view of the stream:")
print(json.dumps(fold_sse(stream), indent=2))
