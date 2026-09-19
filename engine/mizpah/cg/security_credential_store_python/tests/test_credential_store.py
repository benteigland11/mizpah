import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.credential_store import CredentialStore, QuarantinedCredential  # noqa: E402

NOW = datetime(2030, 5, 6, 7, 8, 9, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> CredentialStore:
    return CredentialStore(tmp_path / "vault" / "credentials.json", clock=lambda: NOW)


def test_put_get_roundtrip_and_permissions(store: CredentialStore) -> None:
    record = store.put("provider-a", {"access_token": "a", "refresh_token": "r"}, {"account": "acct-1"})
    assert record.updated_at == NOW.isoformat() and record.usable
    fetched = store.get("provider-a")
    assert fetched is not None and fetched.secret["refresh_token"] == "r"
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store.path.parent).st_mode) == 0o700
    assert store.get("missing") is None


def test_public_view_hides_secret(store: CredentialStore) -> None:
    store.put("k", {"access_token": "hidden"}, {"scopes": ["a"]})
    listing = store.list()
    assert listing == [{"key": "k", "metadata": {"scopes": ["a"]}, "updated_at": NOW.isoformat(),
                        "quarantined": False, "quarantine_reason": None}]
    assert "hidden" not in json.dumps(listing)


def test_quarantine_blocks_get_but_keeps_record(store: CredentialStore) -> None:
    store.put("k", {"access_token": "a"})
    store.quarantine("k", "invalid_grant")
    with pytest.raises(QuarantinedCredential, match="invalid_grant"):
        store.get("k")
    peeked = store.peek("k")
    assert peeked is not None and peeked.quarantined and peeked.secret == {"access_token": "a"}
    assert store.put("k", {"access_token": "b"}).usable
    assert store.get("k") is not None


def test_quarantine_missing_key_raises(store: CredentialStore) -> None:
    with pytest.raises(KeyError):
        store.quarantine("nope", "why")


def test_delete(store: CredentialStore) -> None:
    store.put("k", {"a": 1})
    assert store.delete("k") is True
    assert store.delete("k") is False
    assert store.list() == []


def test_atomic_write_leaves_no_temp_files(store: CredentialStore) -> None:
    store.put("k", {"a": 1})
    store.put("j", {"b": 2})
    assert sorted(p.name for p in store.path.parent.iterdir()) == ["credentials.json"]


def test_rejects_foreign_file(tmp_path: Path) -> None:
    path = tmp_path / "creds.json"
    path.write_text('{"something": "else"}')
    with pytest.raises(ValueError):
        CredentialStore(path).list()


def test_default_clock_is_utc(tmp_path: Path) -> None:
    record = CredentialStore(tmp_path / "c.json").put("k", {})
    assert record.updated_at.endswith("+00:00")
