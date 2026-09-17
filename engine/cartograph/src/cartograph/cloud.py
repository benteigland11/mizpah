"""
Cartograph cloud registry client.

All network activity is in this module. Every request is routed through the
dogfooded `infra-urllib-client-python` widget (stdlib-only, so no extra
dependencies), which applies the OS-trust-store TLS context uniformly - there
is no raw urllib here to bypass it. If the user is not authenticated or the
registry is unreachable, functions degrade gracefully and return structured
error dicts rather than raising.

Cloud is additive to local:
- search() merges cloud results with local results (caller deduplicates)
- push() sends a validated widget + signed stamp to the registry
- is_available() does a cheap health check before trying anything

Widget IDs in cloud results are namespaced as @owner/widget-id.
Install paths remain cg/<widget-id>/ regardless of source.
"""

import json
import logging
import os
import urllib.parse
import zipfile
from io import BytesIO

log = logging.getLogger("cartograph")

_TIMEOUT = 10  # seconds


# ---------------------------------------------------------------------------
# @endpoint decorator (dogfooded from infra-http-endpoint-registry-python)
# ---------------------------------------------------------------------------
# Loaded by file path to sidestep the `src/` namespace collision with this
# repo's own src/cartograph/. Metadata attached to each decorated function
# is introspected by scripts/gen_openapi.py to produce the OpenAPI spec.

def _load_endpoint_decorator():
    import importlib.util
    here = os.path.dirname(__file__)
    candidates = [
        os.path.join(here, "..", "..", "cg", "infra_http_endpoint_registry_python",
                     "src", "http_endpoint_registry.py"),
        os.path.join(here, "..", "cg", "infra_http_endpoint_registry_python",
                     "src", "http_endpoint_registry.py"),
    ]
    widget_file = next((c for c in candidates if os.path.isfile(c)), candidates[-1])
    spec = importlib.util.spec_from_file_location("_cg_endpoint_registry", widget_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.endpoint, module.get_endpoints


endpoint, get_endpoints = _load_endpoint_decorator()


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------
# HTTP plumbing is delegated to the dogfooded `infra-urllib-client-python`
# widget. The module-level helpers below (_get/_post/_patch/_delete/
# _post_multipart for JSON, request_raw via _http_client() for binary and
# header-bearing responses) keep their names and signatures so every existing
# caller works unchanged, but their bodies are thin wrappers around a shared
# HTTPClient instance. Nothing here uses raw urllib, so the widget's
# OS-trust-store TLS context applies to every call uniformly.

def _registry_url() -> str:
    from .auth import get_registry_url
    return get_registry_url().rstrip("/")


def _auth_headers(url: str) -> dict:
    """Auth callback for the shared HTTPClient. Called per-request with the
    resolved base URL so token lookup respects registry-specific tokens."""
    from .auth import get_token
    token = get_token(url)
    return {"Authorization": f"Bearer {token}"} if token else {}


def _http_client():
    """Lazily construct the shared HTTPClient. File-path import avoids the
    `src/` namespace collision with this repo's own src/cartograph/."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        return _HTTP_CLIENT
    import importlib.util
    here = os.path.dirname(__file__)
    candidates = [
        os.path.join(here, "..", "..", "cg", "infra_urllib_client_python",
                     "src", "urllib_client.py"),
        os.path.join(here, "..", "cg", "infra_urllib_client_python",
                     "src", "urllib_client.py"),
    ]
    widget_file = next((c for c in candidates if os.path.isfile(c)), candidates[-1])
    spec = importlib.util.spec_from_file_location("_cg_urllib_client", widget_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # base_url is a placeholder — every call passes base_url= explicitly so
    # the client can target any registry via per-request override.
    from . import __version__
    _HTTP_CLIENT = module.HTTPClient(
        base_url="http://placeholder.invalid",
        auth_headers=_auth_headers,
        default_timeout=_TIMEOUT,
        multipart_timeout=30.0,
        user_agent=f"cartograph/{__version__}",
    )
    return _HTTP_CLIENT


_HTTP_CLIENT = None


def _resolve_base(registry_url: str | None) -> str:
    return registry_url.rstrip("/") if registry_url else _registry_url()


def _get(path: str, registry_url: str | None = None) -> dict:
    return _http_client().get(path, base_url=_resolve_base(registry_url))


def _post(path: str, data: dict, registry_url: str | None = None) -> dict:
    return _http_client().post(path, data=data, base_url=_resolve_base(registry_url))


def _patch(path: str, data: dict, registry_url: str | None = None) -> dict:
    return _http_client().patch(path, data=data, base_url=_resolve_base(registry_url))


def _post_multipart(path: str, fields: dict, file_data: bytes, filename: str,
                    registry_url: str | None = None) -> dict:
    """POST multipart/form-data with a single file attachment."""
    return _http_client().post_multipart(
        path,
        fields=fields,
        file_data=file_data,
        filename=filename,
        file_field_name="file",
        file_content_type="application/zip",
        base_url=_resolve_base(registry_url),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """Return True if the registry responds to a health check."""
    try:
        r = _http_client().request_raw("GET", "/health", base_url=_registry_url(), timeout=5)
        return r.get("status") == 200
    except Exception:
        return False


def _validate_widgets(widgets: list, context: str = "") -> list:
    """
    Validate widget objects returned by the cloud registry.

    Required fields (widget is dropped if missing or wrong type):
      - id      (str, format "@owner/widget-id") — used for dedup and lookup
      - version (str, semver)                    — used for sync and upgrade logic

    Expected fields (used by CLI with .get() defaults, should be present for full UX):
      - name, domain, language, description, tags, rating, owner, install_count

    Optional/additive fields (passed through untouched — registry may add freely):
      - relevance_score  defaults to 0 if absent in search results
      - stale, deprecated, last_updated, or any future annotations

    Malformed widgets are dropped with a warning log rather than failing the
    whole response. A broken registry is the registry owner's problem.
    """
    valid = []
    for w in widgets:
        wid = w.get("id", "")
        ver = w.get("version", "")
        if not isinstance(wid, str) or not wid:
            log.warning("Cloud%s: dropping widget missing 'id': %s", f" ({context})" if context else "", w)
            continue
        if not isinstance(ver, str) or not ver:
            log.warning("Cloud%s: dropping widget '%s' missing 'version'", f" ({context})" if context else "", wid)
            continue
        valid.append(w)
    return valid


@endpoint("GET", "/v1/widgets/search",
          response="SearchResponse",
          tags=["Widgets"],
          summary="Search the widget registry")
def search(query: str, domain_filter: str | None = None,
           language_filter: str | None = None, top_k: int = 10,
           registry_url: str | None = None,
           languages: "set[str] | None" = None) -> dict:
    """
    Search the cloud registry (public — no auth required).

    `language_filter` is the single-language `--language` narrowing.
    `languages`, if given, is the set of languages available on this
    machine (from `doctor`/`available_languages()`); the server filters
    results to that set BEFORE ranking and truncating to top_k, so the
    page fills with installable widgets. The caller still re-filters
    client-side as a backstop for servers that don't honor the param.

    Returns {"widgets": [...], "source": "cloud"} on success,
    or {"error": ..., "widgets": []} on failure so the caller can still
    merge partial results.
    """
    params = f"?q={urllib.parse.quote(query)}&top_k={top_k}"
    if domain_filter:
        params += f"&domain={urllib.parse.quote(domain_filter)}"
    if language_filter:
        params += f"&language={urllib.parse.quote(language_filter)}"
    if languages:
        # Sorted for stable URLs (cache-friendly); server treats it as a set.
        params += f"&languages={urllib.parse.quote(','.join(sorted(languages)))}"

    result = _get(f"/v1/widgets/search{params}", registry_url=registry_url)
    if "error" in result:
        return {"widgets": [], "source": "cloud", "error": result["error"]}

    widgets = _validate_widgets(result.get("widgets", []), context="search")
    for w in widgets:
        w.setdefault("relevance_score", 0)
    return {"widgets": widgets, "source": "cloud"}


@endpoint("GET", "/v1/users/search",
          response="UserSearchResponse",
          tags=["Users"],
          summary="Search users by handle or name")
def search_users(query: str, top_k: int = 20) -> dict:
    """
    Search the cloud registry for users by handle/name.

    Returns {"users": [...]} on success, or {"error": ..., "users": []} on failure.
    """
    params = f"?q={urllib.parse.quote(query)}&top_k={top_k}"
    result = _get(f"/v1/users/search{params}")
    if "error" in result:
        return {"users": [], "error": result["error"]}
    return {"users": result.get("users", [])}


@endpoint("POST", "/v1/widgets/{widget_id}/publish",
          response="PushResponse",
          multipart={
              "fields": {
                  "widget_id": "str",
                  "visibility": "str",
                  "stamp": "str",
                  "allowed_extensions": "str",
                  "implementation_hash": "str",
                  "governance": "str",
              },
              "file_field": "file",
              "required": ["widget_id", "stamp"],
          },
          tags=["Widgets"],
          summary="Publish a widget version")
def push(widget_path: str, widget_id: str, visibility: str = "public",
         governance: str | None = None, registry_url: str | None = None) -> dict:
    """
    Push a validated widget to the cloud registry.

    Reads the validation stamp, signs it, bundles src/tests/examples/widget.json
    into a zip, and POSTs to the registry.

    Returns the registry response dict, or {"error": ...} on failure.
    """
    from .auth import is_authenticated
    from .validation_stamp import read_stamp
    from .trust import sign_stamp, MissingSigningKeyError

    if not is_authenticated():
        return {"error": "Not authenticated. Run: cartograph login"}

    stamp = read_stamp(widget_path)
    if stamp is None:
        return {
            "error": (
                f"No validation stamp found at {widget_path}. "
                "Run 'cartograph validate' first."
            )
        }

    # Sign the stamp. A missing signing key means the credentials file
    # was written by an older CLI (id_token only) or got corrupted — the
    # ID token can still satisfy is_authenticated() while the signing key
    # is gone. Surface this as an actionable re-login prompt instead of
    # letting a placeholder-signed stamp hit the server.
    try:
        signature = sign_stamp(stamp)
    except MissingSigningKeyError as e:
        return {"error": (
            "Your credentials are missing a signing key. Run "
            "`cartograph login` to re-authenticate. "
            f"(Details: {e})"
        )}
    signed_stamp = {**stamp, "signature": signature}

    zip_bytes = _zip_widget(widget_path)

    # Send allowed extensions so the cloud can validate dynamically
    # instead of maintaining a hardcoded whitelist per language.
    from .languages.registry import allowed_extensions
    from .engine import calculate_implementation_hash
    fields = {
        "widget_id": widget_id,
        "visibility": visibility,
        "stamp": json.dumps(signed_stamp),
        "allowed_extensions": json.dumps(sorted(allowed_extensions())),
    }
    # Client-computed implementation_hash. The server should echo this back
    # verbatim in the inspect response — re-deriving on the server risks
    # drift from zip canonicalization or file-walk ordering, so the contract
    # is "client hashes, server stores, server echoes." Servers that don't
    # know about this field will ignore it; no harm.
    impl_hash = calculate_implementation_hash(widget_path)
    if impl_hash:
        fields["implementation_hash"] = impl_hash
    if governance:
        fields["governance"] = governance

    result = _post_multipart(
        f"/v1/widgets/{urllib.parse.quote(widget_id, safe='')}/publish",
        fields=fields,
        file_data=zip_bytes,
        filename=f"{widget_id}.zip",
        registry_url=registry_url,
    )
    # Rewrite the server's "bump the version" hint so agents are told to use
    # Cartograph's own versioning flow. The version field in widget.json is
    # managed by the CLI; other fields (description, tags, etc.) are free
    # to edit — this message only guards the version field.
    err_text = str(result.get("error", ""))
    if "already published" in err_text.lower():
        result["error"] = (
            "This version is already published to the cloud. "
            "Cartograph manages the version field in widget.json — don't "
            "bump it by hand. To publish a change (including edits to "
            "description, tags, tests, or examples), run "
            "`cartograph checkin <path> --bump patch --reason \"...\" --publish`."
        )
    return result


@endpoint("GET", "/v1/auth/me",
          response="WhoamiResponse",
          tags=["Auth"],
          summary="Return the current authenticated user's profile")
def whoami(registry_url: str | None = None) -> dict:
    """Return the current user's profile, or {"error": ...}."""
    return _get("/v1/auth/me", registry_url=registry_url)


@endpoint("GET", "/v1/widgets/{owner_handle}/{widget_id}",
          response="InspectResponse",
          tags=["Widgets"],
          summary="Fetch a widget's metadata (optionally with source)")
def inspect(owner_handle: str, widget_id: str, source: bool = False,
            manifest: bool = False,
            registry_url: str | None = None) -> dict:
    """Inspect a cloud widget. Public widgets don't require auth.

    Pass source=True to include extracted src/ file contents in the result.
    Pass manifest=True to include the raw widget.json as stored in the zip
    (used by the checkin no-op guard to compare metadata-only edits).
    """
    path = f"/v1/widgets/{urllib.parse.quote(owner_handle)}/{urllib.parse.quote(widget_id)}"
    params = []
    if source:
        params.append("include_source=true")
    if manifest:
        params.append("include_manifest=true")
    if params:
        path += "?" + "&".join(params)
    return _get(path, registry_url=registry_url)


@endpoint("GET", "/v1/widgets/{owner_handle}/{widget_id}/download",
          responses={
              200: {
                  "description": "Widget zip. X-Widget-Version and X-Widget-Governance response headers carry metadata.",
                  "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
                  "headers": {
                      "X-Widget-Version": {"schema": {"type": "string"}},
                      "X-Widget-Governance": {"schema": {"type": "string"}},
                      "X-Widget-Stamp": {"schema": {"type": "string"}, "description": "Signed validation stamp (compact JSON) stored at publish time"},
                  },
              },
              404: {"description": "Widget not found", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
          },
          tags=["Widgets"],
          summary="Download a widget's zip bundle")
def download_widget(owner_handle: str, widget_id: str,
                    registry_url: str | None = None) -> dict:
    """Download a widget zip from the cloud registry.

    Returns {"zip_bytes": bytes, "version": str} on success,
    or {"error": ...} on failure.
    """
    path = (
        f"/v1/widgets/{urllib.parse.quote(owner_handle)}"
        f"/{urllib.parse.quote(widget_id)}/download"
    )
    r = _http_client().request_raw(
        "GET", path,
        base_url=_resolve_base(registry_url),
        headers={"Accept": "application/zip"},
        timeout=30,
    )
    if "error" in r:
        return r
    # HTTP header names are case-insensitive and proxies rewrite them
    # (Google Front End lowercases everything), so normalize before lookup.
    headers = {k.lower(): v for k, v in r["headers"].items()}
    stamp = None
    raw_stamp = headers.get("x-widget-stamp")
    if raw_stamp:
        try:
            stamp = json.loads(raw_stamp)
        except ValueError:
            stamp = None  # malformed header: fall back to unstamped install
    return {
        "zip_bytes": r["body"],
        "version": headers.get("x-widget-version", "0.0.0"),
        "governance": headers.get("x-widget-governance"),
        "stamp": stamp,
    }


@endpoint("GET", "/v1/registry/info",
          response="RegistryInfoResponse",
          tags=["Registry"],
          summary="Advertise registry capabilities (e.g. supports_visibility)")
def registry_info(registry_url: str | None = None) -> dict:
    """Fetch registry capabilities (e.g. whether it validates widgets).

    Returns {"validates": bool, "allow_blueprints": bool, ...} or
    {"error": ...}. Falls back to {"validates": False} if the endpoint
    doesn't exist yet. The `allow_blueprints` field is absent on older
    registries — callers that care should treat missing as True for
    backwards compatibility (the field was added when the public registry
    chose to opt out).
    """
    result = _get("/v1/registry/info", registry_url=registry_url)
    if "error" in result:
        # Endpoint doesn't exist yet - assume no validation
        return {"validates": False}
    return result


@endpoint("GET", "/v1/widgets",
          response="ListWidgetsResponse",
          tags=["Widgets"],
          summary="List all widgets in the registry")
def list_widgets(top_k: int = 500) -> dict:
    """Return all cloud widgets, or {"error": ...}."""
    return _get(f"/v1/widgets?top_k={top_k}")


def _delete(path: str, registry_url: str | None = None) -> dict:
    return _http_client().delete(path, base_url=_resolve_base(registry_url))


@endpoint("DELETE", "/v1/widgets/{widget_id}",
          response="DeleteResponse",
          tags=["Widgets"],
          summary="Delete a widget from the registry")
def delete_widget(widget_id: str, registry_url: str | None = None) -> dict:
    """Delete a widget from the cloud registry."""
    return _delete(f"/v1/widgets/{urllib.parse.quote(widget_id, safe='')}", registry_url=registry_url)


@endpoint("GET", "/v1/widgets/{owner_handle}/{widget_id}/reviews",
          response="ReviewsResponse",
          tags=["Reviews"],
          summary="List reviews for a widget")
def get_reviews(owner_handle: str, widget_id: str,
                registry_url: str | None = None) -> dict:
    """Fetch reviews for a cloud widget.

    Returns {"reviews": [...], "rating": avg, "review_count": n}
    or {"error": ..., "reviews": []}.
    """
    result = _get(
        f"/v1/widgets/{urllib.parse.quote(owner_handle)}"
        f"/{urllib.parse.quote(widget_id)}/reviews",
        registry_url=registry_url,
    )
    if "error" in result:
        return {"reviews": [], "error": result["error"]}
    return result


@endpoint("POST", "/v1/widgets/{owner_handle}/{widget_id}/rate",
          response="RateResponse",
          tags=["Reviews"],
          summary="Rate a widget 1-5 with an optional comment")
def rate_widget(owner_handle: str, widget_id: str, score: int, comment: str = "",
                registry_url: str | None = None) -> dict:
    """Rate a cloud widget."""
    params = f"?score={score}"
    if comment:
        params += f"&comment={urllib.parse.quote(comment)}"
    return _post(f"/v1/widgets/{urllib.parse.quote(owner_handle)}/{urllib.parse.quote(widget_id)}/rate{params}",
                 {}, registry_url=registry_url)


@endpoint("GET", "/v1/widgets/{owner_handle}/{widget_id}/versions",
          response="VersionsResponse",
          tags=["Versions"],
          summary="List published versions of a widget")
def get_versions(owner_handle: str, widget_id: str,
                 registry_url: str | None = None) -> dict:
    """List available versions for a cloud widget.

    Returns {"versions": [...], "current_version": str}
    or {"error": ..., "versions": []}.
    """
    result = _get(
        f"/v1/widgets/{urllib.parse.quote(owner_handle)}"
        f"/{urllib.parse.quote(widget_id)}/versions",
        registry_url=registry_url,
    )
    if "error" in result:
        return {"versions": [], "error": result["error"]}
    return result


@endpoint("POST", "/v1/widgets/{owner_handle}/{widget_id}/rollback",
          response="RollbackResponse",
          tags=["Versions"],
          summary="Roll a widget back to an earlier published version")
def rollback_widget(owner_handle: str, widget_id: str, version: str,
                    registry_url: str | None = None) -> dict:
    """Roll back a cloud widget to a previous version."""
    params = f"?version={urllib.parse.quote(version)}"
    return _post(
        f"/v1/widgets/{urllib.parse.quote(owner_handle)}"
        f"/{urllib.parse.quote(widget_id)}/rollback{params}",
        {},
        registry_url=registry_url,
    )


@endpoint("GET", "/v1/auth/tos",
          response="TosResponse",
          tags=["Auth"],
          summary="Fetch the registry's terms of service")
def get_tos() -> dict:
    """Fetch current TOS text and version from registry."""
    return _get("/v1/auth/tos")


@endpoint("POST", "/v1/auth/accept-tos",
          response="AcceptTosResponse",
          tags=["Auth"],
          summary="Record that the current user has accepted the ToS")
def accept_tos() -> dict:
    """Accept the current TOS version."""
    return _post("/v1/auth/accept-tos", {})


def check_tos() -> dict:
    """Check if the current user has accepted the latest TOS.

    Returns {"accepted": bool, "current_version": int, "user_version": int}
    or {"error": ...}.
    """
    me = whoami()
    if "error" in me:
        return me
    tos = get_tos()
    if "error" in tos:
        return tos
    return {
        "accepted": me.get("tos_accepted", False),
        "current_version": tos.get("version", 0),
        "user_version": me.get("tos_version", 0),
    }


def login_with_credentials(id_token: str, refresh_token: str,
                           signing_key: str) -> dict:
    """
    Validate an ID token against the registry and save all credentials.

    Returns {"status": "success", "owner": ...} or {"error": ...}.
    """
    from .auth import save_credentials

    # Validate the *candidate* id_token (not the stored one), so override the
    # Authorization header the client's auth callback would otherwise inject.
    data = _http_client().get(
        "/v1/auth/me",
        base_url=_registry_url(),
        headers={"Authorization": f"Bearer {id_token}"},
    )
    if "error" in data:
        code = data.get("status_code")
        if code == 401:
            return {"error": "Invalid token."}
        if code is not None:
            return {"error": f"Registry error: {code}"}
        return {"error": data["error"]}

    save_credentials(id_token, refresh_token, signing_key)
    return {"status": "success", "owner": data.get("owner") or data.get("username", "unknown")}


# ---------------------------------------------------------------------------
# Governance & Proposals
# ---------------------------------------------------------------------------

@endpoint("PATCH", "/v1/widgets/{owner_handle}/{widget_id}",
          response="UpdateWidgetResponse",
          request="UpdateWidgetResponse",
          tags=["Widgets"],
          summary="Update a widget's settings (governance, visibility)")
def update_widget(owner_handle: str, widget_id: str,
                  registry_url: str | None = None, **kwargs) -> dict:
    """PATCH a cloud widget's settings (e.g. governance)."""
    return _patch(
        f"/v1/widgets/{urllib.parse.quote(owner_handle)}"
        f"/{urllib.parse.quote(widget_id)}",
        kwargs,
        registry_url=registry_url,
    )


_ZIP_WARN_BYTES = 5 * 1024 * 1024     # 5 MB — likely an artifact slipped through
_ZIP_ERROR_BYTES = 25 * 1024 * 1024   # 25 MB — fail before the server 413s

# Compiled / linker output extensions that should never appear in a widget
# zip. The cloud rejects anything outside its allowlist; we pre-flight here
# so the user gets the offending paths locally instead of after a failed
# upload. This is a denylist (not the cloud's full allowlist) to avoid
# duplicating that table — it only catches the recurring "build artifact
# under a non-standard cache dir slipped past the exclude list" failure.
_BLOCKED_BINARY_EXTS = frozenset({
    ".o", ".obj", ".a", ".lib", ".so", ".dylib", ".dll", ".exe",
    ".class", ".pyc", ".pyo", ".pyd",
})


def _check_for_build_artifacts(buf: BytesIO) -> None:
    """Scan a built zip for compiled artifacts the cloud will reject.

    Raises ValueError listing every offending path. Cheap second pass over
    the zip's central directory — no payload reads.
    """
    offenders: list[str] = []
    with zipfile.ZipFile(buf, "r") as zf:
        for name in zf.namelist():
            ext = os.path.splitext(name)[1].lower()
            if ext in _BLOCKED_BINARY_EXTS:
                offenders.append(name)
    if offenders:
        sample = "\n  ".join(offenders[:10])
        more = "" if len(offenders) <= 10 else f"\n  ... ({len(offenders) - 10} more)"
        raise ValueError(
            f"Widget zip contains {len(offenders)} build artifact(s) the "
            f"registry will reject:\n  {sample}{more}\n"
            "These are likely from a build cache (e.g. nimcache, target/, "
            "build/) under a non-standard path. Delete them from the widget "
            "directory and re-run."
        )


def _zip_widget(widget_path: str) -> bytes:
    """Bundle widget files into a zip in memory (shared by push and propose).

    Excludes build artifacts using the universal build-artifact-ignore
    widget — universal entries (.git, .idea, ...) plus the widget's
    language-specific entries (node_modules, .angular, vendor, target, ...).
    Adds a size guard so payloads that explode locally don't get pushed.
    """
    from cg.universal_build_artifact_ignore_python.src.build_artifact_ignore import (
        excludes_for,
        filter_dirs,
        prefix_excludes_for,
    )

    language = _read_widget_language(widget_path)
    artifacts = excludes_for(language=language) | {"history"}
    prefixes = set(prefix_excludes_for(language=language))
    # If the engine declares an optional sidecar (e.g. openscad's python/
    # calc helpers), union in that language's artifacts too — pytest leaves
    # .pytest_cache at widget root, which would otherwise slip through.
    try:
        from cartograph.languages import get_engine
        engine = get_engine(language)
        sidecar = engine.sidecar() if engine else None
        if sidecar is not None:
            artifacts = artifacts | excludes_for(language=sidecar[0])
            prefixes |= set(prefix_excludes_for(language=sidecar[0]))
    except Exception:
        pass
    skip_files = {".validation_stamp.json", ".file_stamp.json", ".dep_cache.json",
                  "reviews.json", "changelog.json"}

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(widget_path):
            dirs[:] = filter_dirs(dirs, artifacts, prefixes=prefixes)
            for fname in files:
                # Apply both the explicit per-file skip set and the
                # build-artifact set — the latter contains generated files
                # (e.g. nimble.paths) as well as directory names.
                if fname in skip_files or fname in artifacts or fname.endswith(".pyc"):
                    continue
                fpath = os.path.join(root, fname)
                arcname = os.path.relpath(fpath, widget_path)
                zf.write(fpath, arcname)

    _check_for_build_artifacts(buf)

    payload = buf.getvalue()
    size = len(payload)
    if size >= _ZIP_ERROR_BYTES:
        raise ValueError(
            f"Widget zip is {size / 1024 / 1024:.1f} MB, exceeding "
            f"{_ZIP_ERROR_BYTES // 1024 // 1024} MB upload guard. A build "
            "artifact directory likely slipped past the exclude list. "
            "Inspect with `unzip -l` or run `cartograph validate` to "
            "trigger cleanup, then retry."
        )
    if size >= _ZIP_WARN_BYTES:
        log.warning(
            "Widget zip is %.1f MB — unusually large; check for stray build "
            "artifacts (node_modules, .angular, target, vendor, ...).",
            size / 1024 / 1024,
        )
    return payload


def _read_widget_language(widget_path: str) -> str | None:
    """Return tech_stack.language from widget.json, or None if absent."""
    manifest = os.path.join(widget_path, "widget.json")
    try:
        with open(manifest, "r", encoding="utf-8") as f:
            return json.load(f).get("tech_stack", {}).get("language")
    except (OSError, json.JSONDecodeError):
        return None


@endpoint("POST", "/v1/widgets/{owner_handle}/{widget_id}/contribute",
          response="ProposalResponse",
          multipart={
              "fields": {
                  "reason": "str",
                  "stamp": "str",
                  "allowed_extensions": "str",
                  "implementation_hash": "str",
              },
              "file_field": "file",
              "required": ["reason", "stamp"],
          },
          tags=["Proposals"],
          summary="Submit a proposal against someone else's widget")
def propose(widget_path: str, owner_handle: str, widget_id: str,
            reason: str, registry_url: str | None = None) -> dict:
    """Propose a contribution to someone else's widget.

    Validates locally, zips the widget, and POSTs to the contribute endpoint.
    Returns the registry response which can be:
    - published (auto-merged for open governance)
    - proposed (queued for review)
    - escalated (safeguards tripped, queued with violations)
    """
    from .auth import is_authenticated
    from .validation_stamp import read_stamp
    from .trust import sign_stamp, MissingSigningKeyError

    if not is_authenticated():
        return {"error": "Not authenticated. Run: cartograph login"}

    stamp = read_stamp(widget_path)
    if stamp is None:
        return {"error": f"No validation stamp at {widget_path}. Run 'cartograph validate' first."}

    try:
        signature = sign_stamp(stamp)
    except MissingSigningKeyError as e:
        return {"error": (
            "Your credentials are missing a signing key. Run "
            "`cartograph login` to re-authenticate. "
            f"(Details: {e})"
        )}
    signed_stamp = {**stamp, "signature": signature}

    zip_bytes = _zip_widget(widget_path)

    from .languages.registry import allowed_extensions
    from .engine import calculate_implementation_hash
    fields = {
        "reason": reason,
        "stamp": json.dumps(signed_stamp),
        "allowed_extensions": json.dumps(sorted(allowed_extensions())),
    }
    # See push() for rationale — client hashes, server echoes.
    impl_hash = calculate_implementation_hash(widget_path)
    if impl_hash:
        fields["implementation_hash"] = impl_hash

    return _post_multipart(
        f"/v1/widgets/{urllib.parse.quote(owner_handle)}"
        f"/{urllib.parse.quote(widget_id)}/contribute",
        fields=fields,
        file_data=zip_bytes,
        filename=f"{widget_id}.zip",
        registry_url=registry_url,
    )


@endpoint("GET", "/v1/auth/my-proposals",
          response="ProposalListResponse",
          tags=["Proposals"],
          summary="List proposals submitted by the current user")
def my_proposals(registry_url: str | None = None) -> dict:
    """List the authenticated user's proposals."""
    return _get("/v1/auth/my-proposals", registry_url=registry_url)


@endpoint("GET", "/v1/auth/my-widgets",
          response="MyWidgetsResponse",
          tags=["Widgets"],
          summary="List widgets owned by the current user (public and private)")
def list_my_widgets(registry_url: str | None = None) -> list[dict]:
    """Return the authenticated user's cloud widgets (public and private), or empty list on failure."""
    result = _get("/v1/auth/my-widgets", registry_url=registry_url)
    if "error" in result:
        return []
    return _validate_widgets(result.get("widgets", []), context="my-widgets")


@endpoint("GET", "/v1/widgets/{owner_handle}/{widget_id}/proposals",
          response="ProposalListResponse",
          tags=["Proposals"],
          summary="List proposals against a widget (owner view)")
def list_proposals(owner_handle: str, widget_id: str,
                   registry_url: str | None = None) -> dict:
    """List proposals for a widget (owner view)."""
    return _get(
        f"/v1/widgets/{urllib.parse.quote(owner_handle)}"
        f"/{urllib.parse.quote(widget_id)}/proposals",
        registry_url=registry_url,
    )


@endpoint("GET", "/v1/widgets/{owner_handle}/{widget_id}/proposals/{proposal_id}/diff",
          tags=["Proposals"],
          summary="Fetch a proposal's unified diff (text)")
def proposal_diff(owner_handle: str, widget_id: str,
                  proposal_id: str, registry_url: str | None = None) -> dict:
    """Fetch the unified diff for a proposal.

    Returns {"diff": str} on success, or {"error": ...} on failure.
    """
    path = (
        f"/v1/widgets/{urllib.parse.quote(owner_handle)}"
        f"/{urllib.parse.quote(widget_id)}"
        f"/proposals/{urllib.parse.quote(proposal_id)}/diff"
    )
    r = _http_client().request_raw(
        "GET", path,
        base_url=_resolve_base(registry_url),
        headers={"Accept": "text/plain"},
        timeout=30,
    )
    if "error" in r:
        return r
    return {"diff": r["body"].decode("utf-8", errors="replace")}


@endpoint("POST", "/v1/widgets/{owner_handle}/{widget_id}/proposals/{proposal_id}/accept",
          tags=["Proposals"],
          summary="Accept a proposal into the widget")
def accept_proposal(owner_handle: str, widget_id: str,
                    proposal_id: str, registry_url: str | None = None) -> dict:
    """Accept a proposal."""
    return _post(
        f"/v1/widgets/{urllib.parse.quote(owner_handle)}"
        f"/{urllib.parse.quote(widget_id)}"
        f"/proposals/{urllib.parse.quote(proposal_id)}/accept",
        {},
        registry_url=registry_url,
    )


@endpoint("POST", "/v1/widgets/{owner_handle}/{widget_id}/proposals/{proposal_id}/reject",
          tags=["Proposals"],
          summary="Reject a proposal with an optional reason")
def reject_proposal(owner_handle: str, widget_id: str,
                    proposal_id: str, reason: str = "",
                    registry_url: str | None = None) -> dict:
    """Reject a proposal."""
    params = f"?reason={urllib.parse.quote(reason)}" if reason else ""
    return _post(
        f"/v1/widgets/{urllib.parse.quote(owner_handle)}"
        f"/{urllib.parse.quote(widget_id)}"
        f"/proposals/{urllib.parse.quote(proposal_id)}/reject{params}",
        {},
        registry_url=registry_url,
    )


# ---------------------------------------------------------------------------
# Blueprints (v0.7) — composition manifests with pinned leaf deps.
# Tarball transport. Cloud handles all storage/Firestore details; these
# wrappers are pure HTTP plumbing.
# ---------------------------------------------------------------------------

def publish_blueprint(blueprint_id: str, tarball: bytes,
                      visibility: str = "public",
                      registry_url: str | None = None) -> dict:
    """POST a packed blueprint tarball to the cloud.

    The tarball must contain a top-level blueprint.json whose 'id' matches
    `blueprint_id` and whose 'version' is the version being published.

    Returns {"status": "success", "version": ..., "namespaced_id": ...}
    on success, or {"error": ...} on failure.
    """
    from .auth import is_authenticated
    if not is_authenticated():
        return {"error": "Not authenticated. Run: cartograph login"}

    return _http_client().post_multipart(
        f"/v1/blueprints/{urllib.parse.quote(blueprint_id, safe='')}/publish",
        fields={"visibility": visibility},
        file_data=tarball,
        filename=f"{blueprint_id}.tar.gz",
        file_field_name="file",
        file_content_type="application/gzip",
        base_url=_resolve_base(registry_url),
    )


def inspect_blueprint(owner_handle: str, blueprint_id: str,
                      registry_url: str | None = None) -> dict:
    """Fetch blueprint metadata from the cloud. Returns {"error": ...} on
    miss; the install-side cloud-version probe relies on that to fall back
    to first-publish."""
    return _get(
        f"/v1/blueprints/{urllib.parse.quote(owner_handle)}"
        f"/{urllib.parse.quote(blueprint_id)}",
        registry_url=registry_url,
    )


def download_blueprint(owner_handle: str, blueprint_id: str,
                       version: str | None = None,
                       registry_url: str | None = None) -> dict:
    """Download a blueprint tarball.

    Returns {"tar_bytes": bytes, "version": str} on success,
    or {"error": ...} on failure.
    """
    path = (
        f"/v1/blueprints/{urllib.parse.quote(owner_handle)}"
        f"/{urllib.parse.quote(blueprint_id)}/download"
    )
    if version:
        path += f"?version={urllib.parse.quote(version)}"
    r = _http_client().request_raw(
        "GET", path,
        base_url=_resolve_base(registry_url),
        headers={"Accept": "application/gzip"},
        timeout=30,
    )
    if "error" in r:
        return r
    return {
        "tar_bytes": r["body"],
        "version": r["headers"].get("X-Blueprint-Version", version or "0.0.0"),
    }
