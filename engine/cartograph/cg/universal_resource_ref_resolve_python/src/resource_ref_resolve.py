"""Resolve a module reference from the three common CLI forms.

Supported locators (caller supplies existence facts; this module is pure):

  1. **Bare id** — ``backend-retry-python``
  2. **Library id** — same token with ``lib=True`` (``--lib``)
  3. **Directory path** — ``cg/backend_retry_python``, ``.``, absolute path

Also classifies ``@owner/id`` cloud refs so callers can branch without
re-parsing.

The widget never touches the filesystem. The CLI (or any host) probes
paths and the library index, then passes a :class:`ResolveFacts` snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

TokenKind = Literal["empty", "cloud_ref", "path_like", "bare_id"]
ResolveVia = Literal["lib", "dir", "id", "cwd", "cloud", "none"]
ModuleKind = Literal["widget", "blueprint", "unknown", "cloud"]


@dataclass(frozen=True)
class LibraryEntry:
    """One installable module known to the host library."""

    id: str
    path: str
    kind: str = "widget"  # "widget" | "blueprint"


@dataclass(frozen=True)
class ResolveFacts:
    """Host-provided facts about the token/path (no I/O inside the widget).

    Absolute paths only; empty string means "not available".
    """

    token_dir: str = ""
    """Abs path if the first token is a publishable directory."""

    path_dir: str = ""
    """Abs path if the explicit path arg is a publishable directory."""

    cwd_dir: str = ""
    """Abs path if cwd itself is a publishable directory."""

    lib_path: str = ""
    """Abs path from library index when token matches an id."""

    lib_kind: str = "widget"
    """``widget`` or ``blueprint`` for the library match."""

    token_manifest_id: str = ""
    path_manifest_id: str = ""
    cwd_manifest_id: str = ""
    token_is_blueprint: bool = False
    path_is_blueprint: bool = False
    cwd_is_blueprint: bool = False


@dataclass(frozen=True)
class ResolveResult:
    """Outcome of resolution."""

    ok: bool
    id: Optional[str] = None
    path: Optional[str] = None
    kind: ModuleKind = "unknown"
    via: ResolveVia = "none"
    error: Optional[str] = None

    @property
    def is_cloud(self) -> bool:
        return self.kind == "cloud"


def classify_token(token: Optional[str]) -> TokenKind:
    """Classify a CLI token without filesystem access."""
    if token is None or not str(token).strip():
        return "empty"
    t = str(token).strip()
    if t.startswith("@"):
        return "cloud_ref"
    if t in (".", "..") or "/" in t or "\\" in t:
        return "path_like"
    if t.startswith("./") or t.startswith(".\\") or t.startswith("../"):
        return "path_like"
    return "bare_id"


def parse_cloud_ref(token: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Split ``@owner/id`` into (owner, bare_id, error)."""
    t = (token or "").strip()
    if not t.startswith("@"):
        return None, None, "not a cloud ref"
    rest = t[1:]
    if "/" not in rest:
        return None, None, "cloud ref must be @owner/widget-id"
    owner, bare = rest.split("/", 1)
    if not owner or not bare:
        return None, None, "cloud ref must be @owner/widget-id"
    return owner, bare, None


def resolve_module_ref(
    token: Optional[str] = None,
    *,
    path: Optional[str] = None,
    lib: bool = False,
    facts: Optional[ResolveFacts] = None,
) -> ResolveResult:
    """Resolve a local (or cloud) module reference from token + path + lib.

    Priority:

      1. ``lib=True`` → library id (token required)
      2. token is a directory → that tree; id from its manifest
      3. path is a directory → that tree; id from manifest (token may be id)
      4. bare token hits library → library path
      5. token empty and cwd is a module tree → cwd
      6. cloud ``@owner/id`` → kind=cloud (no local path)
      7. failure with a stable error string
    """
    facts = facts or ResolveFacts()
    token_s = (token or "").strip() or None
    path_s = (path or "").strip() or None
    kind_tok = classify_token(token_s)

    # --- --lib <id> --------------------------------------------------------
    if lib:
        if not token_s:
            return ResolveResult(
                ok=False, error="library lookup requires a module id")
        if kind_tok == "cloud_ref":
            return ResolveResult(
                ok=False,
                error="--lib does not accept cloud @owner/id refs",
            )
        if kind_tok == "path_like":
            return ResolveResult(
                ok=False,
                error="--lib expects a module id, not a directory path",
            )
        if not facts.lib_path:
            return ResolveResult(
                ok=False,
                error=f"module '{token_s}' not found in library",
            )
        mid = facts.token_manifest_id or token_s
        mk: ModuleKind = (
            "blueprint" if facts.lib_kind == "blueprint" else "widget"
        )
        return ResolveResult(
            ok=True, id=mid, path=facts.lib_path, kind=mk, via="lib")

    # --- @owner/id ---------------------------------------------------------
    if kind_tok == "cloud_ref":
        owner, bare, err = parse_cloud_ref(token_s or "")
        if err:
            return ResolveResult(ok=False, error=err)
        return ResolveResult(
            ok=True, id=bare, path=None, kind="cloud", via="cloud")

    # --- directory via first token ----------------------------------------
    if facts.token_dir:
        mid = facts.token_manifest_id or None
        mk = "blueprint" if facts.token_is_blueprint else "widget"
        if not mid and not facts.token_is_blueprint:
            return ResolveResult(
                ok=False,
                path=facts.token_dir,
                error="directory has no readable module id in its manifest",
            )
        return ResolveResult(
            ok=True,
            id=mid or token_s,
            path=facts.token_dir,
            kind=mk,
            via="dir",
        )

    # --- explicit path directory (second arg or default ".") --------------
    if facts.path_dir:
        mid = facts.path_manifest_id or (
            token_s if kind_tok == "bare_id" else None
        )
        mk = "blueprint" if facts.path_is_blueprint else "widget"
        if not mid and not facts.path_is_blueprint:
            return ResolveResult(
                ok=False,
                path=facts.path_dir,
                error="directory has no readable module id in its manifest",
            )
        return ResolveResult(
            ok=True,
            id=mid,
            path=facts.path_dir,
            kind=mk,
            via="dir" if path_s not in (None, ".") else "cwd",
        )

    # --- bare id → library -------------------------------------------------
    if kind_tok == "bare_id" and facts.lib_path:
        mid = facts.token_manifest_id or token_s
        mk = "blueprint" if facts.lib_kind == "blueprint" else "widget"
        return ResolveResult(
            ok=True, id=mid, path=facts.lib_path, kind=mk, via="id")

    # --- cwd only (token empty, path default) -----------------------------
    if kind_tok == "empty" and facts.cwd_dir:
        mid = facts.cwd_manifest_id or None
        mk = "blueprint" if facts.cwd_is_blueprint else "widget"
        if not mid and not facts.cwd_is_blueprint:
            return ResolveResult(
                ok=False,
                path=facts.cwd_dir,
                error="cwd is a module tree but manifest id is missing",
            )
        return ResolveResult(
            ok=True, id=mid, path=facts.cwd_dir, kind=mk, via="cwd")

    # --- failures ----------------------------------------------------------
    if kind_tok == "bare_id":
        return ResolveResult(
            ok=False,
            error=(
                f"module '{token_s}' not found as a directory or library id"
            ),
        )
    if kind_tok == "path_like":
        return ResolveResult(
            ok=False,
            error=f"not a widget or blueprint directory: {token_s}",
        )
    return ResolveResult(
        ok=False,
        error=(
            "could not determine module; pass an id, a directory, "
            "or use --lib <id>"
        ),
    )
