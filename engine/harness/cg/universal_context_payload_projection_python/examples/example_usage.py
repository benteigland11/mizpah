from pathlib import Path
import json
import sys


sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.context_payload_projection import fit_native_messages, project_payload_content


payload = "\n".join(f"result row {index}" for index in range(40))
projection = {
    "decision": "summarize",
    "reason": "old_large",
    "name": "query_result",
    "status": "success",
    "token_count": 1600,
}

result = project_payload_content(payload, projection=projection, max_summary_chars=160)
print(result.content)

# Character counts stand in for a tokenizer in this offline example only.
messages = [dict(role='user', content='Keep the fixed reference.'),
            dict(role='assistant', content='Observed measurements: '+('value '*2000))]
view = fit_native_messages(messages, count_tokens=lambda items:len(json.dumps(items)),
    token_budget=900, protected_prefix=1, recent_exchanges=1, max_field_characters=200,
    archive_reference='saved/messages.json')
assert view['projected'] and view['prompt_tokens'] <= 900
print(view['messages'])
