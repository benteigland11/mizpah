"""Translate a Chat Completions request and fold a canned Responses stream back."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.chat_responses_codec import chat_to_responses, fold_sse

request = chat_to_responses({
    "model": "example-model",
    "messages": [
        {"role": "system", "content": "You answer in one line."},
        {"role": "user", "content": "What is the capital of France?"},
    ],
    "tools": [{"type": "function", "function": {"name": "lookup", "description": "Look a fact up",
                                                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}],
    "max_tokens": 64,
    "seed": 1,
})
print("Responses body:")
print(json.dumps(request.body, indent=2))
print("dropped Chat-only fields:", request.dropped_fields)

events = [
    {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "role": "assistant"}},
    {"type": "response.output_text.delta", "output_index": 0, "delta": "Paris."},
    {"type": "response.completed", "response": {
        "id": "resp_demo", "model": "example-model", "status": "completed",
        "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Paris."}]}],
        "usage": {"input_tokens": 21, "output_tokens": 3, "total_tokens": 24}}},
]
stream = []
for event in events:
    stream += [f"event: {event['type']}\n", f"data: {json.dumps(event)}\n", "\n"]

print("\nChat Completions view of the stream:")
print(json.dumps(fold_sse(stream), indent=2))
