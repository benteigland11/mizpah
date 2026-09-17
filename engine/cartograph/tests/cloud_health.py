#!/usr/bin/env python3
"""
Cloud registry health check.

Hits every endpoint the Cartograph CLI and dashboard depend on and reports
which ones are live.  Useful for verifying your own registry deployment.

Usage:
    python tests/cloud_health.py                          # uses configured registry
    CARTOGRAPH_REGISTRY_URL=https://... python tests/cloud_health.py
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TIMEOUT = 5  # seconds per request

# A widget ID that should exist for read-path tests.
# Falls back gracefully if it doesn't.
SAMPLE_OWNER = "test"
SAMPLE_WIDGET = "backend-retry-backoff-python"


def get_registry_url():
    """Get registry URL from env or cartograph auth config."""
    url = __import__("os").environ.get("CARTOGRAPH_REGISTRY_URL")
    if url:
        return url.rstrip("/")
    try:
        from cartograph.auth import get_registry_url as _get_url
        return _get_url().rstrip("/")
    except Exception:
        return None


def get_token():
    """Get auth token if available."""
    try:
        from cartograph.auth import get_token as _get_token
        return _get_token()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers(token=None):
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _check(method, url, token=None, data=None):
    """Make a request and return (status_code, body_dict, elapsed_ms)."""
    headers = _headers(token)
    payload = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            elapsed = int((time.time() - start) * 1000)
            body = json.loads(resp.read())
            return resp.status, body, elapsed
    except urllib.error.HTTPError as e:
        elapsed = int((time.time() - start) * 1000)
        try:
            body = json.loads(e.read())
        except Exception:
            body = {}
        return e.code, body, elapsed
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return 0, {"error": str(e)}, elapsed


# ---------------------------------------------------------------------------
# Endpoint definitions
# ---------------------------------------------------------------------------

def build_checks(base_url, token):
    """Return list of (name, method, url, data, expected_codes)."""
    o = urllib.parse.quote(SAMPLE_OWNER)
    w = urllib.parse.quote(SAMPLE_WIDGET)

    return [
        # Auth
        ("GET  /v1/auth/me",
         "GET", f"{base_url}/v1/auth/me", None, {200, 401}),
        ("GET  /v1/auth/tos",
         "GET", f"{base_url}/v1/auth/tos", None, {200, 404}),

        # Registry info
        ("GET  /v1/registry/info",
         "GET", f"{base_url}/v1/registry/info", None, {200, 404}),

        # Widget listing & search
        ("GET  /v1/widgets?top_k=1",
         "GET", f"{base_url}/v1/widgets?top_k=1", None, {200}),
        ("GET  /v1/widgets/search?q=test",
         "GET", f"{base_url}/v1/widgets/search?q=test", None, {200}),

        # User search
        ("GET  /v1/users/search?q=test",
         "GET", f"{base_url}/v1/users/search?q=test", None, {200, 404}),

        # Widget detail (may 404 if sample doesn't exist)
        (f"GET  /v1/widgets/{SAMPLE_OWNER}/{SAMPLE_WIDGET}",
         "GET", f"{base_url}/v1/widgets/{o}/{w}", None, {200, 404}),
        (f"GET  /v1/widgets/{SAMPLE_OWNER}/{SAMPLE_WIDGET}/files",
         "GET", f"{base_url}/v1/widgets/{o}/{w}/files", None, {200, 404}),
        (f"GET  /v1/widgets/{SAMPLE_OWNER}/{SAMPLE_WIDGET}/reviews",
         "GET", f"{base_url}/v1/widgets/{o}/{w}/reviews", None, {200, 404}),
        (f"GET  /v1/widgets/{SAMPLE_OWNER}/{SAMPLE_WIDGET}/versions",
         "GET", f"{base_url}/v1/widgets/{o}/{w}/versions", None, {200, 404}),
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    base_url = get_registry_url()
    if not base_url:
        print("  No registry URL found.")
        print("  Set CARTOGRAPH_REGISTRY_URL or run `cartograph login` first.")
        sys.exit(1)

    token = get_token()

    print(f"\n  Registry:  {base_url}")
    print(f"  Auth:      {'token present' if token else 'no token'}")
    print()

    checks = build_checks(base_url, token)
    passed = 0
    failed = 0
    warnings = 0

    for name, method, url, data, expected in checks:
        status, body, ms = _check(method, url, token, data)

        if status in expected:
            tag = "[ok]"
            passed += 1
        elif status == 0:
            tag = "[ERR]"
            failed += 1
        elif status == 404:
            tag = "[404]"
            warnings += 1
        elif status == 405:
            tag = "[405]"
            failed += 1
        else:
            tag = f"[{status}]"
            failed += 1

        pad = 55 - len(name)
        print(f"  {tag:6s} {name}{' ' * max(pad, 1)}{ms:4d}ms")
        if status == 0:
            print(f"         {body.get('error', '?')}")

    print()
    print(f"  {passed} passed, {failed} failed, {warnings} warnings")

    if failed > 0:
        print(f"\n  {failed} endpoint(s) not responding as expected.")
        sys.exit(1)
    else:
        print("\n  All endpoints healthy!")


if __name__ == "__main__":
    main()
