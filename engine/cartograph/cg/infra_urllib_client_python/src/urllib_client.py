"""Stdlib-only HTTP client with structured error dicts.

Built for self-describing API clients that want consistent error handling
without dragging in requests/httpx. Every method returns either the parsed
JSON body (dict) or an error dict with {"error": str, "status_code": int | None}.
No exceptions leak for HTTP or network failures.

    client = HTTPClient(
        base_url="https://registry.example.com",
        auth_headers=lambda url: {"Authorization": f"Bearer {token_for(url)}"},
    )
    result = client.get("/v1/widgets/some-id")
    if "error" in result:
        log.warning("lookup failed: %s", result["error"])
    else:
        use(result)

base_url is resolved once at construction. Per-call `base_url=` overrides it
when the same client needs to target a different host (e.g. multi-registry
setups where the URL is part of the dispatch decision).
"""

import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable


# ---------------------------------------------------------------------------
# TLS trust store
# ---------------------------------------------------------------------------
#
# stdlib `ssl` verifies against OpenSSL's default cert path. On some platforms
# that path is empty or stale, so verification fails with
# "unable to get local issuer certificate" even though the OS itself trusts
# the certificate. The clearest example is the python.org macOS build: it
# ships an empty cert directory, so every HTTPS call fails until the user runs
# `Install Certificates.command` by hand.
#
# Rather than push that chore onto users (or take a hard dependency on a
# bundled cert package), we build the verification context from the trust
# store the OS already maintains. On macOS that means reading the system
# keychains via the built-in `security` tool, which also picks up any root a
# corporate proxy / MDM has installed - the certifi route would still fail
# behind such a proxy. We never disable verification.


def _macos_keychain_pem(keychains: list | None = None) -> str:
    """Export trusted root certs from the macOS keychains as concatenated PEM.

    Passing no keychain searches the user's default keychain search list
    (login + System); SystemRootCertificates is added explicitly since Apple's
    bundled roots live there. Returns "" if the `security` tool is unavailable.
    """
    if keychains is None:
        keychains = [None, "/System/Library/Keychains/SystemRootCertificates.keychain"]
    chunks: list[str] = []
    for keychain in keychains:
        cmd = ["/usr/bin/security", "find-certificate", "-a", "-p"]
        if keychain:
            cmd.append(keychain)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except Exception:
            continue
        if result.returncode == 0 and result.stdout:
            chunks.append(result.stdout)
    return "\n".join(chunks)


def build_ssl_context() -> ssl.SSLContext:
    """Return a verifying SSL context backed by the OS trust store.

    Starts from the stdlib default (hostname checking + CERT_REQUIRED, never
    relaxed) and augments it with roots the OS trusts but OpenSSL's default
    path may miss. On macOS that's the system keychains, read with the
    built-in `security` tool - no bundled cert package required.
    """
    context = ssl.create_default_context()
    if sys.platform == "darwin":
        try:
            pem = _macos_keychain_pem()
            if pem.strip():
                context.load_verify_locations(cadata=pem)
        except Exception:
            pass
    return context


_DEFAULT_CONTEXT: ssl.SSLContext | None = None


def default_ssl_context() -> ssl.SSLContext:
    """Process-wide cached context from build_ssl_context() (built once)."""
    global _DEFAULT_CONTEXT
    if _DEFAULT_CONTEXT is None:
        _DEFAULT_CONTEXT = build_ssl_context()
    return _DEFAULT_CONTEXT


class HTTPClient:
    """HTTP client for JSON APIs. All methods return body dict or error dict."""

    def __init__(
        self,
        base_url: str,
        auth_headers: Callable[[str], dict] | None = None,
        default_timeout: float = 10.0,
        multipart_timeout: float = 30.0,
        user_agent: str | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ):
        if not isinstance(base_url, str) or not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.auth_headers = auth_headers
        self.default_timeout = default_timeout
        self.multipart_timeout = multipart_timeout
        self.user_agent = user_agent
        # Caller may inject a context; otherwise fall back to the shared
        # OS-trust-store-backed default, resolved lazily on first request so
        # constructing a client never triggers the keychain export.
        self._ssl_context = ssl_context

    def _get_ssl_context(self) -> ssl.SSLContext:
        if self._ssl_context is None:
            self._ssl_context = default_ssl_context()
        return self._ssl_context

    def _resolve_base(self, base_url_override: str | None) -> str:
        if base_url_override:
            return base_url_override.rstrip("/")
        return self.base_url

    def _build_headers(self, resolved_base: str, extra: dict | None,
                       content_type: str | None = "application/json") -> dict:
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        if self.auth_headers:
            try:
                auth = self.auth_headers(resolved_base) or {}
            except Exception:
                auth = {}
            headers.update(auth)
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        base_url: str | None,
        headers: dict | None,
        timeout: float | None,
        content_type: str | None,
    ) -> dict:
        resolved_base = self._resolve_base(base_url)
        url = resolved_base + path
        request_headers = self._build_headers(resolved_base, headers, content_type)
        req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        # context is used only for https targets; urlopen ignores it for http.
        context = self._get_ssl_context() if url.lower().startswith("https") else None
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.default_timeout,
                                        context=context) as resp:
                raw = resp.read()
                if not raw:
                    return {}
                try:
                    return json.loads(raw)
                except ValueError:
                    return {"error": "Non-JSON response body", "status_code": resp.status}
        except Exception as e:
            return self._error_dict(e)

    @staticmethod
    def _error_dict(e: Exception) -> dict:
        """Map a request exception to the shared {error, status_code} shape."""
        if isinstance(e, urllib.error.HTTPError):
            err_body = {}
            try:
                err_body = json.loads(e.read())
            except Exception:
                pass
            detail = err_body.get("detail", str(e)) if isinstance(err_body, dict) else str(e)
            return {"error": detail, "status_code": e.code}
        if isinstance(e, urllib.error.URLError):
            return {"error": f"Network error: {e.reason}", "status_code": None}
        return {"error": str(e), "status_code": None}

    def request_raw(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        base_url: str | None = None,
        headers: dict | None = None,
        timeout: float | None = None,
        content_type: str | None = None,
    ) -> dict:
        """Make a request and return the unparsed response.

        Returns {"status": int, "headers": dict, "body": bytes} on success,
        or the same {"error", "status_code"} shape as the JSON methods on
        failure. Use this for binary payloads (zip/tar downloads), endpoints
        whose metadata rides in response headers, or health checks where only
        the status code matters. The TLS context and auth headers are applied
        exactly as for the JSON methods, so callers never touch raw urllib.
        """
        resolved_base = self._resolve_base(base_url)
        url = resolved_base + path
        request_headers = self._build_headers(resolved_base, headers, content_type)
        req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        context = self._get_ssl_context() if url.lower().startswith("https") else None
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.default_timeout,
                                        context=context) as resp:
                return {
                    "status": resp.status,
                    "headers": dict(resp.headers.items()),
                    "body": resp.read(),
                }
        except Exception as e:
            return self._error_dict(e)

    def get(self, path: str, base_url: str | None = None,
            headers: dict | None = None, timeout: float | None = None) -> dict:
        return self._request("GET", path, None, base_url, headers, timeout, content_type=None)

    def post(self, path: str, data: dict | None = None, base_url: str | None = None,
             headers: dict | None = None, timeout: float | None = None) -> dict:
        body = json.dumps(data or {}).encode()
        return self._request("POST", path, body, base_url, headers, timeout, content_type="application/json")

    def patch(self, path: str, data: dict, base_url: str | None = None,
              headers: dict | None = None, timeout: float | None = None) -> dict:
        body = json.dumps(data).encode()
        return self._request("PATCH", path, body, base_url, headers, timeout, content_type="application/json")

    def delete(self, path: str, base_url: str | None = None,
               headers: dict | None = None, timeout: float | None = None) -> dict:
        return self._request("DELETE", path, None, base_url, headers, timeout, content_type=None)

    def post_multipart(
        self,
        path: str,
        fields: dict,
        file_data: bytes | None = None,
        filename: str | None = None,
        file_field_name: str = "file",
        file_content_type: str = "application/octet-stream",
        base_url: str | None = None,
        headers: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        """POST multipart/form-data with zero or one file attachment.

        `fields` are sent as plain form parts (stringified). When `file_data`
        is provided, `filename` is required and the part is sent under
        `file_field_name` with `file_content_type`.
        """
        if file_data is not None and not filename:
            raise ValueError("filename is required when file_data is provided")

        boundary = b"urllib_client_boundary_" + os.urandom(8).hex().encode()
        parts: list[bytes] = []
        for key, value in fields.items():
            parts.append(
                b"--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="' + str(key).encode() + b'"\r\n\r\n'
                + str(value).encode() + b"\r\n"
            )
        if file_data is not None:
            parts.append(
                b"--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="' + file_field_name.encode()
                + b'"; filename="' + filename.encode() + b'"\r\n'
                b"Content-Type: " + file_content_type.encode() + b"\r\n\r\n"
                + file_data + b"\r\n"
            )
        parts.append(b"--" + boundary + b"--\r\n")
        body = b"".join(parts)
        content_type = f"multipart/form-data; boundary={boundary.decode()}"

        return self._request(
            "POST", path, body,
            base_url=base_url,
            headers=headers,
            timeout=timeout or self.multipart_timeout,
            content_type=content_type,
        )
