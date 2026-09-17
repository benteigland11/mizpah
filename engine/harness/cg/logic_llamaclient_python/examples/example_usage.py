"""
Example usage of LlamaClient.

Demonstrates API usage with no network calls — shows how to construct
the client and use helper utilities like extract_xml_tool_calls.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.llamaclient import LlamaClient, extract_xml_tool_calls, get_llama_base_url

# Show how URL is resolved from env or explicit param
url = get_llama_base_url("http://localhost:58080/v1")
print(f"Base URL: {url}")

# Client construction (no network call)
client = LlamaClient(
    base_url="http://localhost:58080/v1",
    default_model="llama3",
    timeout=60,
    known_issues={"repetition": {"identical_tool_calls": 8}},
)
print(f"Client: {client.base_url}, model={client.default_model}")

# Parse XML tool calls from model output
model_output = '''
I'll search for that.
<tool_call>
{"name": "web_search", "arguments": {"query": "weather today"}}
</tool_call>
'''
calls, clean_text = extract_xml_tool_calls(model_output)
print(f"Tool calls parsed: {len(calls)}")
print(f"  name={calls[0]['name']}, args={calls[0]['arguments']}")
print(f"  clean text: '{clean_text.strip()}'")

# Vision message helper (no network)
msg = LlamaClient.create_image_message(
    role="user",
    text="What is in this image?",
    image_url="https://example.com/photo.jpg",
)
print(f"Vision message role={msg['role']}, content parts={len(msg['content'])}")
