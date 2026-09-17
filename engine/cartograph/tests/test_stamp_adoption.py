"""
Stamp-carrying sync/install: a registry-transported validation stamp is
adopted only when its fingerprint matches the extracted bytes (and, for
own-account sync pulls, its HMAC signature checks out). GitHub issue #20.
"""
import json
import os

import pytest

from cartograph.validation_stamp import (
    STAMP_FILE,
    adopt_registry_stamp,
    read_stamp,
    write_stamp,
)


@pytest.fixture
def widget_dir(tmp_path):
    """A minimal on-disk python widget, stamped then un-stamped, plus the
    stamp dict a registry would have stored at publish time."""
    wdir = tmp_path / "universal-demo-python"
    (wdir / "src").mkdir(parents=True)
    (wdir / "tests").mkdir()
    manifest = {
        "meta": {"id": "universal-demo-python", "name": "Demo",
                 "version": "1.0.0", "tags": ["demo"], "domain": "universal"},
        "description": "demo",
        "tech_stack": {"language": "python", "dependencies": []},
    }
    (wdir / "widget.json").write_text(json.dumps(manifest))
    (wdir / "src" / "__init__.py").write_text("")
    (wdir / "src" / "demo.py").write_text("def demo(): return 1\n")
    (wdir / "tests" / "test_demo.py").write_text("def test_demo(): assert True\n")

    from cartograph.languages import get_engine
    engine = get_engine("python")
    write_stamp(str(wdir), "python", engine)
    stamp = read_stamp(str(wdir))
    assert stamp is not None
    os.remove(wdir / STAMP_FILE)
    return wdir, stamp


def test_adopt_matching_stamp(widget_dir):
    wdir, stamp = widget_dir
    assert adopt_registry_stamp(str(wdir), stamp) is True
    adopted = read_stamp(str(wdir))
    assert adopted["fingerprint"] == stamp["fingerprint"]
    # Adoption transports the original validation event, not a new one.
    assert adopted["validated_at"] == stamp["validated_at"]


def test_adopted_stamp_passes_integrity_gate(widget_dir):
    wdir, stamp = widget_dir
    from cartograph.validation_stamp import has_valid_stamp
    assert has_valid_stamp(str(wdir), "python") is False
    adopt_registry_stamp(str(wdir), stamp)
    assert has_valid_stamp(str(wdir), "python") is True


def test_tampered_bytes_refuse_stamp(widget_dir):
    wdir, stamp = widget_dir
    (wdir / "src" / "demo.py").write_text("def demo(): return 666\n")
    assert adopt_registry_stamp(str(wdir), stamp) is False
    assert read_stamp(str(wdir)) is None


def test_no_stamp_is_noop(widget_dir):
    wdir, _ = widget_dir
    assert adopt_registry_stamp(str(wdir), None) is False
    assert adopt_registry_stamp(str(wdir), {}) is False
    assert read_stamp(str(wdir)) is None


def test_signature_required_and_valid(widget_dir, monkeypatch):
    wdir, stamp = widget_dir
    monkeypatch.setenv("CARTOGRAPH_SIGNING_KEY", "test-key")
    from cartograph.trust import sign_stamp
    signed = {**stamp, "signature": sign_stamp(stamp)}
    assert adopt_registry_stamp(str(wdir), signed, require_signature=True) is True
    # Signature is stripped on adoption: local stamps never carry one.
    assert "signature" not in read_stamp(str(wdir))


def test_signature_required_and_wrong(widget_dir, monkeypatch):
    wdir, stamp = widget_dir
    monkeypatch.setenv("CARTOGRAPH_SIGNING_KEY", "test-key")
    signed = {**stamp, "signature": "0" * 64}
    assert adopt_registry_stamp(str(wdir), signed, require_signature=True) is False
    assert read_stamp(str(wdir)) is None


def test_signature_missing_falls_back_to_fingerprint(widget_dir, monkeypatch):
    """Legacy publishes may have unsigned stamps; fingerprint still gates."""
    wdir, stamp = widget_dir
    monkeypatch.setenv("CARTOGRAPH_SIGNING_KEY", "test-key")
    assert adopt_registry_stamp(str(wdir), stamp, require_signature=True) is False


def test_download_widget_parses_stamp_header(monkeypatch):
    from cartograph import cloud

    stamp = {"fingerprint": "abc", "language": "python",
             "validated_at": "2026-01-01T00:00:00+00:00", "engine_version": 1}

    class _FakeClient:
        def request_raw(self, *a, **k):
            return {
                "body": b"zipbytes",
                "headers": {
                    # Google Front End lowercases response headers; the
                    # client must do case-insensitive lookup.
                    "x-widget-version": "1.2.3",
                    "x-widget-stamp": json.dumps(stamp),
                },
            }

    monkeypatch.setattr(cloud, "_http_client", lambda: _FakeClient())
    r = cloud.download_widget("owner", "universal-demo-python")
    assert r["stamp"] == stamp
    assert r["version"] == "1.2.3"


def test_download_widget_malformed_stamp_header(monkeypatch):
    from cartograph import cloud

    class _FakeClient:
        def request_raw(self, *a, **k):
            return {"body": b"z", "headers": {"x-widget-stamp": "not json"}}

    monkeypatch.setattr(cloud, "_http_client", lambda: _FakeClient())
    r = cloud.download_widget("owner", "universal-demo-python")
    assert r["stamp"] is None


def test_download_widget_canonical_case_headers(monkeypatch):
    """Direct-origin responses keep canonical casing; lookup must be
    case-insensitive in both directions."""
    from cartograph import cloud

    class _FakeClient:
        def request_raw(self, *a, **k):
            return {"body": b"z", "headers": {"X-Widget-Version": "2.0.0"}}

    monkeypatch.setattr(cloud, "_http_client", lambda: _FakeClient())
    r = cloud.download_widget("owner", "universal-demo-python")
    assert r["version"] == "2.0.0"


def test_download_widget_no_stamp_header(monkeypatch):
    from cartograph import cloud

    class _FakeClient:
        def request_raw(self, *a, **k):
            return {"body": b"z", "headers": {"X-Widget-Version": "1.0.0"}}  # canonical case must also work

    monkeypatch.setattr(cloud, "_http_client", lambda: _FakeClient())
    r = cloud.download_widget("owner", "universal-demo-python")
    assert r["stamp"] is None
