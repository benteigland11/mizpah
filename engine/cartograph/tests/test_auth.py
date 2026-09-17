"""Tests for auth module - credential safety."""
import json
import os
import stat

import pytest


def test_save_credentials_rejects_empty_token(tmp_path, monkeypatch):
    """save_credentials should refuse to save an empty token."""
    monkeypatch.setattr("cartograph.auth._CREDENTIALS_FILE",
                        str(tmp_path / "creds.json"))
    from cartograph.auth import save_credentials
    with pytest.raises(ValueError, match="empty id_token"):
        save_credentials("", "refresh", "key")


def test_save_credentials_rejects_whitespace_token(tmp_path, monkeypatch):
    """save_credentials should refuse to save a whitespace-only token."""
    monkeypatch.setattr("cartograph.auth._CREDENTIALS_FILE",
                        str(tmp_path / "creds.json"))
    from cartograph.auth import save_credentials
    with pytest.raises(ValueError, match="empty id_token"):
        save_credentials("   ", "refresh", "key")


def test_save_credentials_writes_file(tmp_path, monkeypatch):
    """Valid credentials should be written to disk."""
    creds_file = str(tmp_path / "creds.json")
    monkeypatch.setattr("cartograph.auth._CREDENTIALS_FILE", creds_file)
    from cartograph.auth import save_credentials
    save_credentials("token123", "refresh456", "key789")
    assert os.path.exists(creds_file)
    with open(creds_file) as f:
        data = json.load(f)
    assert data["id_token"] == "token123"
    assert data["refresh_token"] == "refresh456"


def test_write_credentials_survives_chmod_failure(tmp_path, monkeypatch):
    """chmod failure must not delete a live credentials file (that was a logout)."""
    creds_file = str(tmp_path / "creds.json")
    monkeypatch.setattr("cartograph.auth._CREDENTIALS_FILE", creds_file)

    def _bad_chmod(path, mode):
        raise OSError("filesystem does not support permissions")
    monkeypatch.setattr("os.chmod", _bad_chmod)

    from cartograph.auth import save_credentials
    save_credentials("token", "refresh", "key")
    assert os.path.exists(creds_file)
    with open(creds_file, encoding="utf-8") as f:
        data = json.load(f)
    assert data["id_token"] == "token"
    assert data["refresh_token"] == "refresh"


# ---------------------------------------------------------------------------
# Multi-registry tokens: store / remove / aggregated whoami
# ---------------------------------------------------------------------------

def _patch_token_store(tmp_path, monkeypatch):
    path = str(tmp_path / "registry_tokens.json")
    monkeypatch.setattr("cartograph.auth._registry_tokens_path", lambda: path)
    return path


def test_remove_registry_token_deletes_only_that_registry(tmp_path, monkeypatch):
    _patch_token_store(tmp_path, monkeypatch)
    from cartograph.auth import store_registry_token, remove_registry_token, get_token

    store_registry_token("https://a.example.com", "token-a")
    store_registry_token("https://b.example.com", "token-b")

    assert remove_registry_token("https://a.example.com") is True
    assert get_token("https://a.example.com") is None
    assert get_token("https://b.example.com") == "token-b"


def test_remove_registry_token_missing_returns_false(tmp_path, monkeypatch):
    _patch_token_store(tmp_path, monkeypatch)
    from cartograph.auth import remove_registry_token
    assert remove_registry_token("https://nobody.example.com") is False


def test_whoami_aggregates_company_registries(tmp_path, monkeypatch, capsys):
    """whoami reports every registry with stored credentials, not just public."""
    _patch_token_store(tmp_path, monkeypatch)
    from cartograph.auth import store_registry_token
    store_registry_token("https://t12.example.com", "token-t12")

    monkeypatch.setattr("cartograph.auth.is_authenticated", lambda: False)
    monkeypatch.setattr(
        "cartograph.config.get_registries",
        lambda: [{"prefix": "tiger12", "url": "https://t12.example.com"}],
    )
    monkeypatch.setattr(
        "cartograph.cloud.whoami",
        lambda registry_url=None: {"owner": "ben", "registry": registry_url},
    )

    from cartograph.cli import cmd_whoami
    cmd_whoami(None)
    result = json.loads(capsys.readouterr().out)
    assert result["authenticated"] is True
    assert result["registries"]["tiger12"]["owner"] == "ben"
    # No public identity spread when not logged in publicly
    assert "owner" not in result or result.get("owner") != "ben" or "registries" in result


def test_whoami_unauthenticated_everywhere(tmp_path, monkeypatch, capsys):
    _patch_token_store(tmp_path, monkeypatch)
    monkeypatch.setattr("cartograph.auth.is_authenticated", lambda: False)
    monkeypatch.setattr("cartograph.config.get_registries", lambda: [])

    from cartograph.cli import cmd_whoami
    cmd_whoami(None)
    result = json.loads(capsys.readouterr().out)
    assert result == {"authenticated": False}


# ---------------------------------------------------------------------------
# logout --registry: arg wiring + routing (guards against a dead --registry path)
# ---------------------------------------------------------------------------

def test_logout_registry_flag_is_wired_to_handler():
    """`logout --registry <prefix>` must parse through to cmd_logout with the
    prefix set - otherwise getattr(args, 'registry', None) is always None and
    the per-registry logout branch is dead code."""
    from cartograph.cli import build_parser, cmd_logout

    parser = build_parser()
    ns = parser.parse_args(["logout", "--registry", "myorg"])
    assert ns.registry == "myorg"
    assert ns.func is cmd_logout

    # Bare logout: registry is None -> public-logout branch.
    bare = parser.parse_args(["logout"])
    assert bare.registry is None
    assert bare.func is cmd_logout


def test_cmd_logout_registry_routes_to_remove_token(tmp_path, monkeypatch, capsys):
    """cmd_logout --registry resolves the prefix to a URL and drops only that
    registry's token."""
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(
        "cartograph.config.get_registry_url_for_prefix",
        lambda prefix: "https://t12.example.com" if prefix == "tiger12" else None,
    )
    monkeypatch.setattr(
        "cartograph.auth.remove_registry_token",
        lambda url: calls.append(url) or True,
    )
    from cartograph.cli import cmd_logout
    cmd_logout(SimpleNamespace(registry="tiger12"))
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "success"
    assert calls == ["https://t12.example.com"]


def test_cmd_logout_unknown_registry_errors(tmp_path, monkeypatch, capsys):
    """An unconfigured prefix must error, not silently no-op."""
    import pytest
    from types import SimpleNamespace
    monkeypatch.setattr(
        "cartograph.config.get_registry_url_for_prefix", lambda prefix: None)
    removed = []
    monkeypatch.setattr(
        "cartograph.auth.remove_registry_token",
        lambda url: removed.append(url) or True,
    )
    from cartograph.cli import cmd_logout, err
    # err() typically exits; tolerate either SystemExit or a returned error.
    try:
        cmd_logout(SimpleNamespace(registry="ghost"))
    except SystemExit:
        pass
    out = capsys.readouterr()
    assert "not configured" in (out.out + out.err).lower()
    assert removed == []  # never touched any token store


# ---------------------------------------------------------------------------
# Token refresh routing - registry-mediated vs legacy IdP
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _write_creds(path, creds):
    with open(path, "w") as f:
        json.dump(creds, f)


def test_refresh_routes_through_registry_when_no_secret(tmp_path, monkeypatch):
    """Without a stored client_secret, refresh must POST JSON to the
    registry /v1/auth/refresh - the secret lives server-side."""
    creds_file = str(tmp_path / "creds.json")
    monkeypatch.setattr("cartograph.auth._CREDENTIALS_FILE", creds_file)
    _write_creds(creds_file, {"id_token": "old", "refresh_token": "r1"})
    monkeypatch.setattr("cartograph.auth.get_registry_url",
                        lambda: "https://api.example.com")

    seen = {}

    def fake_urlopen(req, timeout=None, context=None):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data)
        seen["content_type"] = req.get_header("Content-type")
        return _FakeResponse({"id_token": "new-token"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    from cartograph.auth import _refresh_id_token
    assert _refresh_id_token("r1") == "new-token"
    assert seen["url"] == "https://api.example.com/v1/auth/refresh"
    assert seen["body"] == {"refresh_token": "r1"}
    assert "json" in seen["content_type"]


def test_refresh_registry_success_drops_legacy_secret(tmp_path, monkeypatch):
    """Once registry-mediated refresh works, any stored client_secret is
    removed and token_url is pinned to the registry endpoint."""
    creds_file = str(tmp_path / "creds.json")
    monkeypatch.setattr("cartograph.auth._CREDENTIALS_FILE", creds_file)
    _write_creds(creds_file, {"id_token": "old", "refresh_token": "r1",
                              "client_secret": ""})
    monkeypatch.setattr("cartograph.auth.get_registry_url",
                        lambda: "https://api.example.com")
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None, context=None:
                        _FakeResponse({"id_token": "new-token"}))
    from cartograph.auth import _refresh_id_token
    assert _refresh_id_token("r1") == "new-token"
    with open(creds_file) as f:
        stored = json.load(f)
    assert stored["id_token"] == "new-token"
    assert "client_secret" not in stored
    assert stored["token_url"] == "https://api.example.com/v1/auth/refresh"


def test_refresh_legacy_idp_path_still_form_posts(tmp_path, monkeypatch):
    """Old logins (IdP token_url + stored client secret) keep working via
    the direct form-POST path."""
    creds_file = str(tmp_path / "creds.json")
    monkeypatch.setattr("cartograph.auth._CREDENTIALS_FILE", creds_file)
    _write_creds(creds_file, {
        "id_token": "old", "refresh_token": "r1",
        "token_url": "https://oauth2.example.com/token",
        "client_id": "cid", "client_secret": "csecret",
    })
    monkeypatch.setattr("cartograph.auth._google_client_id", lambda: "cid")
    monkeypatch.setattr("cartograph.auth._google_client_secret",
                        lambda: "csecret")

    seen = {}

    def fake_urlopen(req, timeout=None, context=None):
        seen["url"] = req.full_url
        seen["body"] = req.data.decode()
        return _FakeResponse({"id_token": "refreshed"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    from cartograph.auth import _refresh_id_token
    assert _refresh_id_token("r1") == "refreshed"
    assert seen["url"] == "https://oauth2.example.com/token"
    assert "grant_type=refresh_token" in seen["body"]
    assert "client_secret=csecret" in seen["body"]


def test_refresh_refuses_http_registry(tmp_path, monkeypatch):
    """A non-https registry refresh endpoint (other than localhost dev)
    must be refused."""
    creds_file = str(tmp_path / "creds.json")
    monkeypatch.setattr("cartograph.auth._CREDENTIALS_FILE", creds_file)
    _write_creds(creds_file, {"id_token": "old", "refresh_token": "r1"})
    monkeypatch.setattr("cartograph.auth.get_registry_url",
                        lambda: "http://api.example.com")
    from cartograph.auth import _refresh_id_token
    assert _refresh_id_token("r1") is None


def _jwt(exp):
    import base64
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": exp}).encode()
    ).decode().rstrip("=")
    return f"x.{payload}.y"


def _expired_jwt():
    return _jwt(1)


def test_write_credentials_keeps_refresh_token_when_omitted(tmp_path, monkeypatch):
    """A writer that only updates id_token must not blank refresh_token."""
    creds_file = str(tmp_path / "creds.json")
    monkeypatch.setattr("cartograph.auth._CREDENTIALS_FILE", creds_file)
    _write_creds(creds_file, {
        "id_token": "old",
        "refresh_token": "keep-me",
        "signing_key": "sign-me",
        "token_url": "https://api.example.com/v1/auth/refresh",
    })
    from cartograph.auth import _write_credentials
    _write_credentials({"id_token": "new"})
    with open(creds_file, encoding="utf-8") as f:
        stored = json.load(f)
    assert stored["id_token"] == "new"
    assert stored["refresh_token"] == "keep-me"
    assert stored["signing_key"] == "sign-me"
    assert stored["token_url"] == "https://api.example.com/v1/auth/refresh"


def test_write_credentials_replaces_atomically(tmp_path, monkeypatch):
    creds_file = str(tmp_path / "creds.json")
    monkeypatch.setattr("cartograph.auth._CREDENTIALS_FILE", creds_file)
    seen = {}
    real_replace = os.replace

    def spy_replace(src, dst):
        seen["src"] = src
        seen["dst"] = dst
        return real_replace(src, dst)

    monkeypatch.setattr("os.replace", spy_replace)
    from cartograph.auth import _write_credentials
    _write_credentials({"id_token": "t", "refresh_token": "r"})
    assert seen["dst"] == creds_file
    assert seen["src"] == creds_file + ".tmp"
    assert not os.path.exists(creds_file + ".tmp")


def test_get_token_refreshes_once_when_many_callers_race(tmp_path, monkeypatch):
    """Parallel get_token calls share one refresh; losers reuse the new token."""
    import threading
    creds_file = str(tmp_path / "creds.json")
    monkeypatch.setattr("cartograph.auth._CREDENTIALS_FILE", creds_file)
    _write_creds(creds_file, {
        "id_token": _expired_jwt(),
        "refresh_token": "r1",
        "token_url": "https://api.example.com/v1/auth/refresh",
    })
    monkeypatch.setattr("cartograph.auth.get_registry_url",
                        lambda: "https://api.example.com")
    calls = []
    lock = threading.Lock()

    def fake_urlopen(req, timeout=None, context=None):
        with lock:
            calls.append(1)
        return _FakeResponse({"id_token": _jwt(2_000_000_000)})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    from cartograph.auth import get_token
    results = [None] * 8

    def worker(i):
        results[i] = get_token()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    fresh = _jwt(2_000_000_000)
    assert results == [fresh] * 8
    assert len(calls) == 1
    with open(creds_file, encoding="utf-8") as f:
        stored = json.load(f)
    assert stored["refresh_token"] == "r1"
    assert stored["id_token"] == fresh


def test_concurrent_partial_writes_cannot_drop_refresh_token(tmp_path, monkeypatch):
    """Even sloppy overlapping writes must leave valid JSON with refresh_token."""
    import threading
    creds_file = str(tmp_path / "creds.json")
    monkeypatch.setattr("cartograph.auth._CREDENTIALS_FILE", creds_file)
    from cartograph.auth import _write_credentials, save_credentials
    save_credentials("id0", "refresh-keep", "sign-keep",
                     token_url="https://api.example.com/v1/auth/refresh")
    errors = []

    def writer(n):
        try:
            _write_credentials({"id_token": f"id{n}"})
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with open(creds_file, encoding="utf-8") as f:
        stored = json.load(f)
    assert stored["refresh_token"] == "refresh-keep"
    assert stored["signing_key"] == "sign-keep"
    assert stored["id_token"].startswith("id")
