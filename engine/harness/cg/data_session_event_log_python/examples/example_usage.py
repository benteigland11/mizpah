from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.session_event_log import SessionEventLog


with TemporaryDirectory() as temp_dir:
    log = SessionEventLog(Path(temp_dir) / "events")
    event = log.append(
        session_id="session_example",
        event_type="user_message",
        payload={"content": "Summarize the failing test."},
        metadata={"mode": "focus"},
    )
    exported = log.export("session_example")

print(event.event_type)
print(len(exported["events"]))
