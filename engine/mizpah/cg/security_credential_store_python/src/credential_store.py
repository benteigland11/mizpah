"""Per-key credential records in one owner-only JSON file.

Three things a local app gets wrong with stored tokens, handled once:

* the file is created ``0600`` and written by atomic replace, so a crash
  mid-write leaves the previous file, not half a token;
* a record has an explicit ``quarantined`` state. When a refresh fails
  terminally the credential is kept for diagnosis but ``get`` will not hand
  it out, so the caller cannot keep retrying a dead grant;
* nothing about *what* a credential is lives here. ``secret`` is an opaque
  dict the caller owns; ``metadata`` is the part that is safe to list.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

FILE_MODE = 0o600
DIR_MODE = 0o700
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CredentialRecord:
    key: str
    secret: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""
    quarantined: bool = False
    quarantine_reason: str | None = None

    @property
    def usable(self) -> bool:
        return not self.quarantined

    def public(self) -> dict[str, Any]:
        """Everything but the secret; safe to print or show."""
        return {
            "key": self.key,
            "metadata": dict(self.metadata),
            "updated_at": self.updated_at,
            "quarantined": self.quarantined,
            "quarantine_reason": self.quarantine_reason,
        }


class QuarantinedCredential(LookupError):
    """The key exists but was quarantined; ``reason`` says why."""

    def __init__(self, key: str, reason: str | None) -> None:
        super().__init__(f"credential {key!r} is quarantined: {reason or 'no reason recorded'}")
        self.key = key
        self.reason = reason


class CredentialStore:
    """One JSON file keyed by credential name.

    ``path`` is the file. The parent directory is created ``0700`` on first
    write. ``clock`` is injected so ``updated_at`` is testable.
    """

    def __init__(self, path: Path | str, *, clock: Callable[[], datetime] | None = None) -> None:
        self._path = Path(path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def path(self) -> Path:
        return self._path

    def get(self, key: str) -> CredentialRecord | None:
        """The usable record for ``key``; ``None`` if absent; raises if quarantined."""
        record = self.peek(key)
        if record is None:
            return None
        if record.quarantined:
            raise QuarantinedCredential(key, record.quarantine_reason)
        return record

    def peek(self, key: str) -> CredentialRecord | None:
        """The record whether or not it is usable."""
        raw = self._read().get(key)
        return None if raw is None else _from_raw(key, raw)

    def put(self, key: str, secret: dict[str, Any], metadata: dict[str, Any] | None = None) -> CredentialRecord:
        """Store or replace; a fresh put always clears quarantine."""
        record = CredentialRecord(key, dict(secret), dict(metadata or {}), self._now())
        self._update(lambda data: data.__setitem__(key, _to_raw(record)))
        return record

    def quarantine(self, key: str, reason: str) -> CredentialRecord:
        current = self.peek(key)
        if current is None:
            raise KeyError(key)
        record = CredentialRecord(key, current.secret, current.metadata, self._now(), True, reason)
        self._update(lambda data: data.__setitem__(key, _to_raw(record)))
        return record

    def delete(self, key: str) -> bool:
        existed = [False]

        def remove(data: dict[str, Any]) -> None:
            existed[0] = data.pop(key, None) is not None

        self._update(remove)
        return existed[0]

    def list(self) -> list[dict[str, Any]]:
        """Public views of every record, quarantined ones included."""
        return [_from_raw(key, raw).public() for key, raw in sorted(self._read().items())]

    def _now(self) -> str:
        return self._clock().astimezone(timezone.utc).isoformat()

    def _read(self) -> dict[str, Any]:
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(document, dict) or document.get("version") != SCHEMA_VERSION:
            raise ValueError(f"{self._path} is not a credential store file")
        records = document.get("records")
        return dict(records) if isinstance(records, dict) else {}

    def _update(self, mutate: Callable[[dict[str, Any]], None]) -> None:
        records = self._read()
        mutate(records)
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        payload = json.dumps({"version": SCHEMA_VERSION, "records": records}, indent=2, sort_keys=True)
        descriptor, temp_name = tempfile.mkstemp(dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, FILE_MODE)
            os.replace(temp_name, self._path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise


def _to_raw(record: CredentialRecord) -> dict[str, Any]:
    return {
        "secret": record.secret,
        "metadata": record.metadata,
        "updated_at": record.updated_at,
        "quarantined": record.quarantined,
        "quarantine_reason": record.quarantine_reason,
    }


def _from_raw(key: str, raw: dict[str, Any]) -> CredentialRecord:
    return CredentialRecord(
        key,
        dict(raw.get("secret") or {}),
        dict(raw.get("metadata") or {}),
        str(raw.get("updated_at") or ""),
        bool(raw.get("quarantined", False)),
        raw.get("quarantine_reason"),
    )
