from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
import json
import os


SCHEMA_VERSION = 1
COMPACTION_EVENT_TYPE = "context_compaction"


@dataclass(frozen=True)
class SessionEvent:
    """One chronological event in a persisted session journal."""

    event_id: str
    session_id: str
    event_type: str
    created_at: str
    payload: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class CompactionRecord:
    """Validated payload for a context compaction event."""

    compaction_id: str
    summary_message_id: str
    compacted_through_event_id: str
    compacted_through_created_at: str
    model: str
    strategy: str
    input_tokens_before: int
    output_tokens_after: int
    dropped_event_count: int
    summarized_event_count: int
    kept_event_count: int
    source_event_ids: tuple[str, ...] = ()
    source_message_ids: tuple[str, ...] = ()
    summary_text: str = ""

    @property
    def token_delta(self) -> int:
        return self.input_tokens_before - self.output_tokens_after

    @property
    def compression_ratio(self) -> float:
        if self.input_tokens_before <= 0:
            return 0.0
        return self.output_tokens_after / self.input_tokens_before


@dataclass(frozen=True)
class CompactionBoundary:
    """Latest compaction boundary used to enforce what can be carried forward."""

    compaction_id: str
    compacted_through_event_id: str
    compacted_through_created_at: str
    summary_message_id: str
    source_event_count: int


class SessionEventLog:
    """Append-only JSONL journal for local session events."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def append(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionEvent:
        resolved_session_id = sanitize_identifier(session_id)
        if not resolved_session_id:
            raise ValueError("session_id is required")
        resolved_event_type = normalize_event_type(event_type)
        event = SessionEvent(
            event_id=f"evt_{uuid4().hex[:16]}",
            session_id=resolved_session_id,
            event_type=resolved_event_type,
            created_at=utc_now_text(),
            payload=json_safe_dict(payload or {}),
            metadata=json_safe_dict(metadata or {}),
        )
        path = self.path_for_session(resolved_session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event_to_dict(event), sort_keys=True, ensure_ascii=False))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def read(self, session_id: str, *, limit: int | None = None) -> tuple[SessionEvent, ...]:
        path = self.path_for_session(session_id)
        if not path.exists():
            return ()
        events: list[SessionEvent] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                    if isinstance(payload, dict):
                        events.append(event_from_dict(payload))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
        if limit is not None and limit >= 0:
            return tuple(events[-limit:])
        return tuple(events)

    def export(self, session_id: str, *, limit: int | None = None) -> dict[str, Any]:
        resolved_session_id = sanitize_identifier(session_id)
        events = self.read(resolved_session_id, limit=limit)
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": resolved_session_id,
            "events": [event_to_dict(event) for event in events],
        }

    def drop_torn_tail(self, session_id: str) -> bool:
        """Remove a final record that has no newline: an append interrupted mid-write (quota, power).

        Only the last line can be torn, and it never committed anywhere else, so dropping it restores
        the journal to its last complete record. Returns True when something was dropped. Complete
        records are never touched; the strict reader stays strict about everything else.
        """
        resolved = sanitize_identifier(session_id)
        if resolved != session_id or not resolved:
            raise ValueError("A canonical session identifier is required")
        path = self.path_for_session(resolved)
        if not path.exists():
            return False
        raw = path.read_bytes()
        if not raw or raw.endswith(b"\n"):
            return False
        cut = raw.rfind(b"\n")
        with path.open("r+b") as handle:
            handle.truncate(cut+1 if cut >= 0 else 0)
        return True

    def read_strict(self, session_id: str) -> tuple[SessionEvent, ...]:
        """Read evidence without skipping, coercing, or inventing missing records.

        Raises ValueError with the offending line number for malformed or partial
        records, schema/identity mismatches, duplicates, and invalid compaction links.
        The legacy tolerant reader remains available for existing consumers.
        """
        resolved = sanitize_identifier(session_id)
        if resolved != session_id or not resolved:
            raise ValueError("A canonical session identifier is required")
        path = self.path_for_session(resolved)
        if not path.exists():
            return ()
        events: list[SessionEvent] = []
        seen: set[str] = set()
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    if not line.endswith("\n"):
                        raise ValueError("incomplete record without a newline")
                    data = json.loads(line, object_pairs_hook=_unique_object,
                                      parse_constant=_reject_constant)
                    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
                        raise ValueError("invalid event schema")
                    for key in ("event_id", "session_id", "event_type", "created_at"):
                        if not isinstance(data.get(key), str) or not data[key].strip():
                            raise ValueError("missing or invalid " + key)
                    if data["session_id"] != resolved or data["event_id"] in seen:
                        raise ValueError("session mismatch or duplicate event identity")
                    if normalize_event_type(data["event_type"]) != data["event_type"]:
                        raise ValueError("noncanonical event type")
                    if parse_utc_datetime(data["created_at"]) is None:
                        raise ValueError("invalid event timestamp")
                    if not isinstance(data.get("payload"), dict) or not isinstance(data.get("metadata"), dict):
                        raise ValueError("payload and metadata must be objects")
                    if data["event_type"] == COMPACTION_EVENT_TYPE:
                        boundary = compaction_from_dict(data["payload"])
                        if boundary.compacted_through_event_id not in seen or any(
                            identity not in seen for identity in boundary.source_event_ids
                        ):
                            raise ValueError("compaction references an absent prior event")
                    events.append(event_from_dict(data))
                    seen.add(data["event_id"])
                except (ValueError, TypeError, KeyError) as error:
                    raise ValueError(f"Journal line {line_number}: {error}") from error
        return tuple(events)

    def append_compaction(
        self,
        *,
        session_id: str,
        compaction: CompactionRecord,
        metadata: dict[str, Any] | None = None,
    ) -> SessionEvent:
        return self.append(
            session_id=session_id,
            event_type=COMPACTION_EVENT_TYPE,
            payload=compaction_to_dict(compaction),
            metadata=metadata,
        )

    def latest_compaction_boundary(self, session_id: str) -> CompactionBoundary | None:
        boundary: CompactionBoundary | None = None
        for event in self.read(session_id):
            if event.event_type != COMPACTION_EVENT_TYPE:
                continue
            try:
                compaction = compaction_from_dict(event.payload)
            except ValueError:
                continue
            boundary = CompactionBoundary(
                compaction_id=compaction.compaction_id,
                compacted_through_event_id=compaction.compacted_through_event_id,
                compacted_through_created_at=compaction.compacted_through_created_at,
                summary_message_id=compaction.summary_message_id,
                source_event_count=len(compaction.source_event_ids),
            )
        return boundary

    def prune_older_than(
        self,
        *,
        retention_days: int,
        now: datetime | None = None,
    ) -> tuple[Path, ...]:
        if retention_days <= 0:
            return ()
        cutoff = normalize_datetime(now) - timedelta(days=retention_days)
        removed: list[Path] = []
        if not self.root.exists():
            return ()
        for path in sorted(self.root.glob("*.jsonl")):
            if not path.is_file():
                continue
            newest_event_at = self._newest_event_timestamp(path)
            if newest_event_at is None:
                newest_event_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if newest_event_at < cutoff:
                path.unlink()
                removed.append(path)
        return tuple(removed)

    def path_for_session(self, session_id: str) -> Path:
        resolved_session_id = sanitize_identifier(session_id)
        if not resolved_session_id:
            raise ValueError("session_id is required")
        return self.root / f"{resolved_session_id}.jsonl"

    def _newest_event_timestamp(self, path: Path) -> datetime | None:
        newest: datetime | None = None
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        payload = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    created_at = parse_utc_datetime(payload.get("created_at"))
                    if created_at is not None and (newest is None or created_at > newest):
                        newest = created_at
        except OSError:
            return None
        return newest


def normalize_event_type(event_type: str) -> str:
    resolved = str(event_type or "").strip().lower().replace(" ", "_")
    resolved = "".join(ch for ch in resolved if ch.isalnum() or ch in {"_", "-", "."})
    if not resolved:
        raise ValueError("event_type is required")
    return resolved


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant: " + value)


def sanitize_identifier(value: str | None) -> str:
    raw = str(value or "").strip()
    return "".join(ch for ch in raw if ch.isalnum() or ch in {"_", "-"})


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def normalize_datetime(value: datetime | None) -> datetime:
    resolved = value or datetime.now(UTC)
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=UTC)
    return resolved.astimezone(UTC)


def parse_utc_datetime(value: Any) -> datetime | None:
    if value in ("", None):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return normalize_datetime(parsed)


def json_safe_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, default=repr, ensure_ascii=False))


def event_to_dict(event: SessionEvent) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event.event_id,
        "session_id": event.session_id,
        "event_type": event.event_type,
        "created_at": event.created_at,
        "payload": json_safe_dict(event.payload),
        "metadata": json_safe_dict(event.metadata),
    }


def event_from_dict(payload: dict[str, Any]) -> SessionEvent:
    return SessionEvent(
        event_id=str(payload.get("event_id", "") or f"evt_{uuid4().hex[:16]}"),
        session_id=sanitize_identifier(str(payload.get("session_id", "") or "")),
        event_type=normalize_event_type(str(payload.get("event_type", "event") or "event")),
        created_at=str(payload.get("created_at", "") or ""),
        payload=json_safe_dict(dict(payload.get("payload", {}) or {})),
        metadata=json_safe_dict(dict(payload.get("metadata", {}) or {})),
    )


def compaction_to_dict(compaction: CompactionRecord) -> dict[str, Any]:
    return {
        "compaction_id": compaction.compaction_id,
        "summary_message_id": compaction.summary_message_id,
        "compacted_through_event_id": compaction.compacted_through_event_id,
        "compacted_through_created_at": compaction.compacted_through_created_at,
        "model": compaction.model,
        "strategy": compaction.strategy,
        "input_tokens_before": compaction.input_tokens_before,
        "output_tokens_after": compaction.output_tokens_after,
        "token_delta": compaction.token_delta,
        "compression_ratio": compaction.compression_ratio,
        "dropped_event_count": compaction.dropped_event_count,
        "summarized_event_count": compaction.summarized_event_count,
        "kept_event_count": compaction.kept_event_count,
        "source_event_ids": list(compaction.source_event_ids),
        "source_message_ids": list(compaction.source_message_ids),
        "summary_text": compaction.summary_text,
    }


def compaction_from_dict(payload: dict[str, Any]) -> CompactionRecord:
    compaction = CompactionRecord(
        compaction_id=str(payload.get("compaction_id", "") or ""),
        summary_message_id=str(payload.get("summary_message_id", "") or ""),
        compacted_through_event_id=str(payload.get("compacted_through_event_id", "") or ""),
        compacted_through_created_at=str(payload.get("compacted_through_created_at", "") or ""),
        model=str(payload.get("model", "") or ""),
        strategy=str(payload.get("strategy", "") or ""),
        input_tokens_before=_non_negative_int(payload.get("input_tokens_before", 0)),
        output_tokens_after=_non_negative_int(payload.get("output_tokens_after", 0)),
        dropped_event_count=_non_negative_int(payload.get("dropped_event_count", 0)),
        summarized_event_count=_non_negative_int(payload.get("summarized_event_count", 0)),
        kept_event_count=_non_negative_int(payload.get("kept_event_count", 0)),
        source_event_ids=tuple(str(value) for value in payload.get("source_event_ids", ()) if str(value).strip()),
        source_message_ids=tuple(str(value) for value in payload.get("source_message_ids", ()) if str(value).strip()),
        summary_text=str(payload.get("summary_text", "") or ""),
    )
    errors = validate_compaction(compaction)
    if errors:
        raise ValueError("; ".join(errors))
    return compaction


def validate_compaction(compaction: CompactionRecord) -> tuple[str, ...]:
    errors: list[str] = []
    if not compaction.compaction_id.strip():
        errors.append("compaction_id is required")
    if not compaction.summary_message_id.strip():
        errors.append("summary_message_id is required")
    if not compaction.compacted_through_event_id.strip():
        errors.append("compacted_through_event_id is required")
    if parse_utc_datetime(compaction.compacted_through_created_at) is None:
        errors.append("compacted_through_created_at must be an ISO timestamp")
    if not compaction.model.strip():
        errors.append("model is required")
    if not compaction.strategy.strip():
        errors.append("strategy is required")
    if compaction.input_tokens_before <= 0:
        errors.append("input_tokens_before must be positive")
    if compaction.output_tokens_after < 0:
        errors.append("output_tokens_after must be non-negative")
    if compaction.output_tokens_after > compaction.input_tokens_before:
        errors.append("output_tokens_after cannot exceed input_tokens_before")
    if not compaction.source_event_ids:
        errors.append("source_event_ids is required")
    if compaction.dropped_event_count + compaction.summarized_event_count + compaction.kept_event_count <= 0:
        errors.append("at least one event count must be positive")
    return tuple(errors)


def _non_negative_int(value: Any) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, resolved)
