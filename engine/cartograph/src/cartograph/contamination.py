"""
Contamination scanner - first stage of the pipeline.

Pipeline order: contamination -> validate -> checkin

Checks for project-specific contamination that would make a widget or
blueprint non-portable. Widgets and blueprints have different contamination
rules — see scan_blueprint_contamination() below for the blueprint contract.

Returns {"blocks": [...], "warnings": [...]}.
  - Blocks are hard failures (absolute paths, credentials). No override.
  - Warnings are quality concerns (hardcoded values, URLs, IPs, env vars,
    unlisted imports). Overridable with --override-warnings --override-reason.
"""

import glob as _glob
import json
import logging
import os
import re

from .engine import _is_os_metadata

log = logging.getLogger("cartograph")


def scan_contamination(path: str) -> dict:
    """Run contamination scanning on a widget or blueprint directory.

    Detects kind via the manifest filename and dispatches accordingly.
    Widgets get the existing language-engine pipeline. Blueprints get the
    sealed-API-surface checks defined in scan_blueprint_contamination().
    """
    from .manifest import detect_kind, KIND_BLUEPRINT, KIND_WIDGET, ManifestError
    try:
        kind = detect_kind(path)
    except ManifestError as e:
        return {"blocks": [str(e)], "warnings": []}

    if kind == KIND_BLUEPRINT:
        return scan_blueprint_contamination(path)
    if kind == KIND_WIDGET:
        return _scan_widget_contamination(path)
    return {
        "blocks": [f"No manifest found in {path} for contamination scan"],
        "warnings": [],
    }


def _scan_widget_contamination(path: str) -> dict:
    """Widget pipeline (unchanged): delegate to language engine."""
    manifest_path = os.path.join(path, "widget.json")
    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {
            "blocks": [f"Could not read widget.json for contamination scan: {e}"],
            "warnings": [],
        }

    tech_stack = data.get("tech_stack", {})
    language = tech_stack.get("language", "python").lower()

    from .languages import get_engine
    engine = get_engine(language)
    if engine is None:
        from .languages.base import LanguageEngine
        engine = LanguageEngine()

    return engine.scan_contamination(path, tech_stack)


# --- Blueprint contamination ------------------------------------------------

# Match `from cg.<dir>...` and `import cg.<dir>...` (Python).
# Captures the immediate cg child directory so we can check it against the
# declared dependency set.
_PY_CG_FROM_RE = re.compile(r"^\s*from\s+cg\.([A-Za-z0-9_]+)")
_PY_CG_IMPORT_RE = re.compile(r"^\s*import\s+cg\.([A-Za-z0-9_]+)")
_PY_CG_BARE_IMPORT_RE = re.compile(r"^\s*import\s+cg(\s|$|,)")


def scan_blueprint_contamination(path: str) -> dict:
    """Sealed-API-surface contamination for a blueprint directory.

    Three rules layered on top of the generic widget-style checks:

      1. src/ may import `cg.<dep_id_dir>` IF and only if the leading dir
         corresponds to a widget declared in `dependencies`.
      2. tests/ MUST NOT import from cg.* at all. Tests consume only src/.
      3. examples/ MUST NOT import from cg.* at all. Examples consume only
         src/.

    The blueprint's own dep widgets are pinned and installed in the
    sandbox at validate time; the rules above guarantee a sealed API
    surface so installers know exactly what to ship.

    Generic widget-style checks (absolute paths, credentials, hardcoded
    IPs) still apply, but the unlisted-imports check is intentionally
    skipped — a blueprint's src/ legitimately imports `cg`, and its
    declared deps are in blueprint.json, not requirements files.
    """
    from .manifest import load_manifest, ManifestError
    from .engine import python_dir_name

    try:
        manifest = load_manifest(path)
    except ManifestError as e:
        return {"blocks": [str(e)], "warnings": []}

    blocks: list[str] = []
    warnings: list[str] = []

    allowed_dep_dirs = {
        python_dir_name(dep["id"]) for dep in manifest.dependencies
    }
    declared_dep_ids = {dep["id"] for dep in manifest.dependencies}

    # Walk src/ (allowed cg imports of declared deps), then tests/ + examples/
    # (no cg imports at all).
    src_files = _collect_files(os.path.join(path, "src"))
    test_files = _collect_files(os.path.join(path, "tests"))
    example_files = _collect_files(os.path.join(path, "examples"))

    for fpath in src_files:
        rel = os.path.relpath(fpath, path)
        for line_no, line, imported_dir in _iter_cg_imports(fpath):
            if imported_dir is None:
                # `import cg` (no submodule) — can't enforce per-dep, block.
                blocks.append(
                    f"Bare `import cg` in {rel}:{line_no} — import the specific "
                    "dep widget module, e.g. `from cg.<dep_id> import ...`"
                )
                continue
            if imported_dir not in allowed_dep_dirs:
                blocks.append(
                    f"src/{rel.split(os.sep, 1)[-1]}:{line_no} imports cg.{imported_dir} "
                    f"which is not a declared dependency. Add it via "
                    f"`cartograph blueprint add-dep <widget_id>` first. "
                    f"Currently declared: {sorted(declared_dep_ids) or '(none)'}"
                )

    for fpath in test_files + example_files:
        rel = os.path.relpath(fpath, path)
        for line_no, line, imported_dir in _iter_cg_imports(fpath):
            target = f"cg.{imported_dir}" if imported_dir else "cg"
            kind = "tests" if fpath in test_files else "examples"
            blocks.append(
                f"{kind}/ may not import from cg/. {rel}:{line_no} imports "
                f"{target}. Tests and examples must consume only what src/ "
                f"exposes — that's the sealed API surface."
            )

    # Generic safety checks (absolute paths, credentials, IPs) on all files.
    blocks.extend(_generic_blocks(src_files + test_files + example_files, path))

    return {"blocks": blocks, "warnings": warnings}


def _collect_files(root: str) -> list[str]:
    """Return all files under `root` (any extension). Skips __pycache__
    and OS metadata (.DS_Store, AppleDouble `._*` forks, __MACOSX)."""
    if not os.path.isdir(root):
        return []
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d != "__pycache__" and not _is_os_metadata(d)]
        for name in filenames:
            if name.endswith(".pyc") or _is_os_metadata(name):
                continue
            out.append(os.path.join(dirpath, name))
    return out


def _iter_cg_imports(fpath: str):
    """Yield (line_no, line_text, imported_cg_subdir_or_None) for cg imports.

    Regex-based on purpose: works across Python source and is harmless on
    non-Python files (no false positives because the patterns require the
    exact `import`/`from` syntax). When v0.7 expands beyond Python this
    will be split per language engine.
    """
    try:
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            for line_no, raw in enumerate(f, 1):
                line = raw.rstrip("\n")
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                m = _PY_CG_FROM_RE.match(line) or _PY_CG_IMPORT_RE.match(line)
                if m:
                    yield line_no, line, m.group(1)
                    continue
                if _PY_CG_BARE_IMPORT_RE.match(line):
                    yield line_no, line, None
    except OSError:
        return


def _generic_blocks(files: list[str], root: str) -> list[str]:
    """Hard-fail checks reused from the widget contamination contract.

    Trimmed copy of the regex-only checks in languages.base — credentials
    and absolute paths. The full unlisted-imports / hardcoded-values
    checks live in the language engines and are skipped for blueprints
    (they're noisy when src/ legitimately imports `cg`).
    """
    abs_path_re = re.compile(
        r'["\'](?:/home/|/Users/|/root/|[A-Za-z]:[/\\\\])[^"\']{3,}["\']'
    )
    credential_re = re.compile(
        r'(?:api_key|api_secret|secret_key|access_token|auth_token|password|passwd|credential)'
        r'\s*=\s*["\'][^"\']{6,}["\']',
        re.IGNORECASE,
    )

    blocks: list[str] = []
    for fpath in files:
        rel = os.path.relpath(fpath, root)
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                for line_no, line in enumerate(f, 1):
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith("//"):
                        continue
                    if abs_path_re.search(line):
                        blocks.append(f"Absolute path in {rel}:{line_no}: {stripped}")
                    if credential_re.search(line):
                        blocks.append(f"Possible credential in {rel}:{line_no}: {stripped}")
        except OSError:
            continue
    return blocks
