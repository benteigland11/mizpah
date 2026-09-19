import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.jwt_claims_unverified import (  # noqa: E402
    MalformedJwt,
    claim,
    expires_at,
    is_expired,
    namespaced_claim,
    unverified_claims,
    unverified_header,
)


def _segment(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def _token(payload: dict, header: dict | None = None) -> str:
    return ".".join([_segment(header or {"alg": "RS256", "typ": "JWT"}), _segment(payload), "sig"])


def test_reads_header_and_claims_without_verifying() -> None:
    token = _token({"sub": "user-1", "https://issuer.example/auth": {"account_id": "acct-9"}})
    assert unverified_header(token)["alg"] == "RS256"
    claims = unverified_claims(token)
    assert claims["sub"] == "user-1"
    assert namespaced_claim(claims, "https://issuer.example/auth", "account_id") == "acct-9"
    assert namespaced_claim(claims, "missing", "account_id", "none") == "none"


def test_claim_path_lookup_prefers_whole_key() -> None:
    claims = {"a/b": 1, "a": {"b": {"c": 2}}}
    assert claim(claims, "a/b") == 1
    assert claim(claims, "a/b/c") == 2
    assert claim(claims, "a/x", "dflt") == "dflt"
    assert claim(claims, "a.b.c", separator=".") == 2


def test_malformed_tokens_raise() -> None:
    for bad in ("", "a.b", "a.b.c.d", "!!!.###.sig", _segment({"alg": "x"}) + ".bm90anNvbg.sig"):
        with pytest.raises(MalformedJwt):
            unverified_claims(bad)
    with pytest.raises(MalformedJwt):
        unverified_header(_segment([1]) + "." + _segment({}) + ".s")


def test_unpadded_segments_decode() -> None:
    assert unverified_claims(_token({"k": "v"}))["k"] == "v"
    assert unverified_claims(_token({"kk": "vv"}))["kk"] == "vv"


def test_expiry_helpers() -> None:
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    exp = int((now + timedelta(seconds=30)).timestamp())
    claims = {"exp": exp}
    assert expires_at(claims) == now + timedelta(seconds=30)
    assert not is_expired(claims, now)
    assert is_expired(claims, now, skew_seconds=60)
    assert is_expired(claims, now + timedelta(seconds=30))
    assert not is_expired({}, now) and expires_at({}) is None
