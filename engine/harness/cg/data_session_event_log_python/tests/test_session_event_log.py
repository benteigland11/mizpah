from pathlib import Path
from datetime import UTC
from datetime import datetime

from src.session_event_log import SessionEventLog
from src.session_event_log import CompactionRecord
from src.session_event_log import compaction_from_dict
from src.session_event_log import validate_compaction
from src.session_event_log import event_from_dict
from src.session_event_log import event_to_dict


def test_session_event_log_appends_and_reads_events(tmp_path: Path) -> None:
    log = SessionEventLog(tmp_path / "events")

    first = log.append(
        session_id="session_123",
        event_type="User Message",
        payload={"content": "hello"},
        metadata={"mode": "focus"},
    )
    second = log.append(
        session_id="session_123",
        event_type="tool.result",
        payload={"output": {"value": 1}},
    )

    events = log.read("session_123")

    assert [event.event_id for event in events] == [first.event_id, second.event_id]
    assert events[0].event_type == "user_message"
    assert events[0].payload == {"content": "hello"}
    assert events[0].metadata == {"mode": "focus"}
    assert events[1].event_type == "tool.result"


def test_session_event_log_exports_limited_tail(tmp_path: Path) -> None:
    log = SessionEventLog(tmp_path)
    log.append(session_id="session_123", event_type="one")
    second = log.append(session_id="session_123", event_type="two")

    exported = log.export("session_123", limit=1)

    assert exported["session_id"] == "session_123"
    assert [event["event_id"] for event in exported["events"]] == [second.event_id]


def test_event_round_trip_preserves_json_safe_payload(tmp_path: Path) -> None:
    log = SessionEventLog(tmp_path)
    event = log.append(
        session_id="session_123",
        event_type="context_snapshot",
        payload={"path": tmp_path},
    )

    restored = event_from_dict(event_to_dict(event))

    assert restored.session_id == event.session_id
    assert restored.event_type == "context_snapshot"
    assert isinstance(restored.payload["path"], str)


def test_prune_older_than_removes_old_session_logs(tmp_path: Path) -> None:
    log = SessionEventLog(tmp_path)
    old_path = log.path_for_session("old")
    fresh_path = log.path_for_session("fresh")
    old_path.write_text(
        '{"created_at":"2026-01-01T00:00:00Z","event_type":"message","session_id":"old"}\n',
        encoding="utf-8",
    )
    fresh_path.write_text(
        '{"created_at":"2026-04-15T00:00:00Z","event_type":"message","session_id":"fresh"}\n',
        encoding="utf-8",
    )

    removed = log.prune_older_than(
        retention_days=90,
        now=datetime(2026, 4, 30, tzinfo=UTC),
    )

    assert removed == (old_path,)
    assert not old_path.exists()
    assert fresh_path.exists()


def test_prune_older_than_zero_disables_cleanup(tmp_path: Path) -> None:
    log = SessionEventLog(tmp_path)
    path = log.path_for_session("old")
    path.write_text(
        '{"created_at":"2026-01-01T00:00:00Z","event_type":"message","session_id":"old"}\n',
        encoding="utf-8",
    )

    removed = log.prune_older_than(
        retention_days=0,
        now=datetime(2026, 4, 30, tzinfo=UTC),
    )

    assert removed == ()
    assert path.exists()


def test_append_compaction_records_enforcement_boundary(tmp_path: Path) -> None:
    log = SessionEventLog(tmp_path)
    first = log.append(session_id="session_123", event_type="message")
    second = log.append(session_id="session_123", event_type="tool_result")
    compaction = CompactionRecord(
        compaction_id="compact_1",
        summary_message_id="msg_summary",
        compacted_through_event_id=second.event_id,
        compacted_through_created_at=second.created_at,
        model="small-model",
        strategy="summarize_drop_dead_weight",
        input_tokens_before=1000,
        output_tokens_after=250,
        dropped_event_count=1,
        summarized_event_count=1,
        kept_event_count=0,
        source_event_ids=(first.event_id, second.event_id),
        source_message_ids=("msg_old",),
        summary_text="Important details remain.",
    )

    event = log.append_compaction(session_id="session_123", compaction=compaction)
    boundary = log.latest_compaction_boundary("session_123")

    assert event.event_type == "context_compaction"
    assert event.payload["token_delta"] == 750
    assert event.payload["compression_ratio"] == 0.25
    assert boundary is not None
    assert boundary.compaction_id == "compact_1"
    assert boundary.compacted_through_event_id == second.event_id
    assert boundary.summary_message_id == "msg_summary"
    assert boundary.source_event_count == 2


def test_compaction_validation_requires_enforcement_fields() -> None:
    compaction = CompactionRecord(
        compaction_id="",
        summary_message_id="",
        compacted_through_event_id="",
        compacted_through_created_at="not-a-date",
        model="",
        strategy="",
        input_tokens_before=0,
        output_tokens_after=1,
        dropped_event_count=0,
        summarized_event_count=0,
        kept_event_count=0,
    )

    errors = validate_compaction(compaction)

    assert "compaction_id is required" in errors
    assert "summary_message_id is required" in errors
    assert "compacted_through_event_id is required" in errors
    assert "source_event_ids is required" in errors
    assert "at least one event count must be positive" in errors


def test_compaction_from_dict_rejects_invalid_payload() -> None:
    try:
        compaction_from_dict({"compaction_id": "compact_1"})
    except ValueError as exc:
        assert "summary_message_id is required" in str(exc)
    else:
        raise AssertionError("invalid compaction payload should fail")


def test_strict_reader_accepts_complete_events_and_empty_session(tmp_path):
    log = SessionEventLog(tmp_path)
    assert log.read_strict('example') == ()
    first = log.append(session_id='example',event_type='request',payload={'id':1})
    second = log.append(session_id='example',event_type='response',payload={'id':1})
    assert log.read_strict('example') == (first,second)


def test_strict_reader_reports_malformed_and_inconsistent_evidence(tmp_path):
    import json
    import pytest
    log = SessionEventLog(tmp_path)
    event = log.append(session_id='example',event_type='request')
    valid = event_to_dict(event)
    invalid_rows = [
        '{', '', '[]', json.dumps(dict(valid,schema_version=99)),
        json.dumps(dict(valid,event_id='')), json.dumps(dict(valid,session_id='another')),
        json.dumps(dict(valid,event_type='Not Canonical')), json.dumps(dict(valid,created_at='bad')),
        json.dumps(dict(valid,payload=[])), json.dumps(dict(valid,metadata=[])),
        json.dumps(valid)[:-1]+',"event_id":"duplicate"}',
        json.dumps(valid).replace('"payload": {}','"payload": {"bad": NaN}'),
    ]
    for text in invalid_rows:
        log.path_for_session('example').write_text(text+'\n')
        with pytest.raises(ValueError,match='Journal line 1'):
            log.read_strict('example')
    log.path_for_session('example').write_text(json.dumps(valid))
    with pytest.raises(ValueError,match='incomplete record'):
        log.read_strict('example')
    log.path_for_session('example').write_text((json.dumps(valid)+'\n')*2)
    with pytest.raises(ValueError,match='Journal line 2'):
        log.read_strict('example')
    with pytest.raises(ValueError,match='canonical session'):
        log.read_strict('../example')


def test_strict_reader_checks_compaction_evidence_links(tmp_path):
    import pytest
    log = SessionEventLog(tmp_path)
    first = log.append(session_id='example',event_type='request')
    compaction = CompactionRecord('rollover','summary',first.event_id,first.created_at,'model','handoff',100,20,0,1,0,(first.event_id,),(), 'Retained result')
    log.append_compaction(session_id='example',compaction=compaction)
    assert len(log.read_strict('example')) == 2
    lines = log.path_for_session('example').read_text().splitlines()
    log.path_for_session('example').write_text(lines[1]+'\n')
    with pytest.raises(ValueError,match='absent prior event'):
        log.read_strict('example')
