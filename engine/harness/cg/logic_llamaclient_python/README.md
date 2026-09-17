# Llama client

`LlamaClient` accepts an explicit `known_issues` policy. Protection is opt-in:

```python
from src.llamaclient import LlamaClient

client = LlamaClient(
    base_url=base_url,
    known_issues={"repetition": {"identical_tool_calls": 8}},
)
result = await client.chat_raw(payload, path="/v1/chat/completions")
```

`chat_raw` preserves the native request fields, including model-specific sampling
and template options, while setting `stream=True` and requesting usage. It assembles
choice indices, fragmented tool identities/names/arguments, content, reasoning,
usage, finish reasons and server metadata. `request_json(path, payload)` posts an
unchanged JSON body, for example to template-rendering and tokenizer endpoints.
`SyncNativeTransport` adapts these single-attempt methods to a synchronous caller,
including a caller already running an event loop.

`stream_raw` events are provisional. Wait for successful iterator exhaustion before
executing accumulated tool calls. Missing terminal markers, missing finish reasons,
malformed streams, size limits and timeouts raise `IncompleteGeneration`; no success
or fabricated usage is emitted. The high-level `stream_chat` publishes assembled
tool calls only after successful completion. Its content and tool delta events remain
provisional. High-level `chat` uses guarded streaming when protection is enabled.

Sustained repetition in content, reasoning or tool arguments closes the HTTP response
before raising `IncompleteGeneration(kind="repetition_detected")`. Optional detection
also counts identical completed function/argument pairs across distinct tool indices.
The error records elapsed time, bounded raw event evidence, resolved policy and the
client's connection closure. Server-side cancellation acknowledgement is not implied.
There are no hidden retries. The application owns recovery and must preserve the
cost and evidence of rejected generations.

The default text detector examines 8,192-character windows every 512 characters,
requiring both high repeated-block coverage and high compressibility. All settings
are configurable through `known_issues.repetition` and returned in audit metadata.
This heuristic can reject intentionally repetitive data. Cross-call detection is
disabled unless `identical_tool_calls` is configured. Resource bounds are explicit;
exceeding them fails the response instead of silently dropping observed channels.

The bundled detector is copied from `backend-streaming-repetition-guard-python`
so this leaf remains self-contained. `repetition-guard-provenance.json` records the
source revision and byte hash. Its independent tests include chunk-boundary checks,
legitimate source/data controls, real loopback cancellation and disabled-policy cases.
