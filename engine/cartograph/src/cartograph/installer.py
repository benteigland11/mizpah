"""
Widget installation and uninstallation.
"""

import glob
import json
import os
import re
import shutil
from .safefs import robust_rmtree
import zipfile
from io import BytesIO


def _long_path_error(e, dest_path):
    """Turn Windows' bare WinError 3/206 into an actionable message when the
    real problem is the 260-char MAX_PATH default. Returns None otherwise."""
    if (os.name == "nt" and isinstance(e, OSError)
            and getattr(e, "winerror", None) in (3, 206)
            and len(dest_path) > 200):
        return {"error": (
            f"Install path is likely too long for Windows "
            f"({len(dest_path)}+ chars): {dest_path}. Windows limits paths "
            f"to 260 characters by default. Install into a shorter target "
            f"path, or enable long paths (set the LongPathsEnabled registry "
            f"value to 1) and retry."
        )}
    return None


def _copy_widget(source_path, dest_path):
    """Copy widget files from library to destination."""
    os.makedirs(dest_path, exist_ok=True)
    try:
        from cg.universal_build_artifact_ignore_python.src.build_artifact_ignore import (
            os_metadata_glob_patterns,
        )
        os_junk = os_metadata_glob_patterns()
    except ImportError:
        os_junk = (".DS_Store", "._*", "__MACOSX", "Thumbs.db", "desktop.ini")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache",
                                    *os_junk)

    for folder in ("src", "tests", "examples"):
        src = os.path.join(source_path, folder)
        if os.path.exists(src):
            shutil.copytree(src, os.path.join(dest_path, folder),
                            dirs_exist_ok=True, ignore=ignore)

    # widget.json
    manifest = os.path.join(source_path, "widget.json")
    if os.path.exists(manifest):
        shutil.copy2(manifest, dest_path)

    # Language project files
    for name in ("Cargo.toml", "Cargo.lock", "go.mod", "go.sum",
                 "package.json", "package-lock.json", "tsconfig.json",
                 "CMakeLists.txt", "Makefile", "pom.xml", "build.gradle"):
        path = os.path.join(source_path, name)
        if os.path.exists(path):
            shutil.copy2(path, dest_path)
    for csproj in glob.glob(os.path.join(source_path, "*.csproj")):
        shutil.copy2(csproj, dest_path)
    for nimble in glob.glob(os.path.join(source_path, "*.nimble")):
        shutil.copy2(nimble, dest_path)


def _widget_dir(target_dir, widget_id):
    """Return the install path: <project_root>/cg/<dir_name>.
    Python widgets get underscores so the directory is importable."""
    from .engine import python_dir_name
    from .engine import DEFAULT_INSTALL_DIR
    return os.path.join(target_dir, DEFAULT_INSTALL_DIR, python_dir_name(widget_id))


def _build_registry_router():
    """Build a PrefixRouter mapping `<prefix>-` to registry URL.

    The trailing hyphen is part of each registered key so that a key
    like `cg-bp-foo` only matches `cg-`, not a prefix-colliding
    registry named `c`.
    """
    from cg.infra_prefix_router_python.src.prefix_router import PrefixRouter
    from .config import get_registries, _PUBLIC_REGISTRY_PREFIX, _PUBLIC_REGISTRY_URL

    router: PrefixRouter = PrefixRouter(default=None)
    router.register(_PUBLIC_REGISTRY_PREFIX + "-", _PUBLIC_REGISTRY_URL)
    for reg in get_registries():
        key = reg["prefix"] + "-"
        if key in router:
            continue
        router.register(key, reg["url"])
    return router


def _resolve_registry(widget_id: str):
    """Check if widget_id starts with a known registry prefix.

    Returns (registry_url, prefix, bare_widget_id) if a prefix is recognized,
    or None if no prefix matches (caller uses default local-first behavior).
    """
    router = _build_registry_router()
    hit = router.match(widget_id)
    if hit is None:
        return None
    key, url = hit
    prefix = key[:-1]
    bare_id = widget_id[len(key):]
    return url, prefix, bare_id


def _install_from_cloud(widget_id, dest_path, registry_url=None, owner_hint=None):
    """Search cloud for a widget and install it by downloading the zip."""
    from .cloud import download_widget

    if owner_hint:
        # Already know the owner from @owner/widget_id format
        owner = owner_hint
    else:
        # Search cloud to find the widget and its owner
        from .cloud import search as cloud_search
        results = cloud_search(widget_id, top_k=5, registry_url=registry_url)
        widgets = results.get("widgets", [])
        match = next(
            (w for w in widgets
             if w.get("id") == widget_id
             or w.get("id", "").endswith(f"/{widget_id}")),
            None,
        )
        if not match:
            registry_label = registry_url or "the cloud registry"
            return {"error": f"Widget '{widget_id}' not found locally or in {registry_label}."}
        owner = match.get("owner", "")
        if not owner:
            return {"error": f"Widget '{widget_id}' found in cloud but missing owner info."}

    result = download_widget(owner, widget_id, registry_url=registry_url)
    if "error" in result:
        return result

    # Extract zip to destination
    zip_bytes = result["zip_bytes"]
    version = result.get("version", "0.0.0")
    governance = result.get("governance")  # from X-Widget-Governance header, None if server doesn't send it
    try:
        from .safefs import safe_extractall
        os.makedirs(dest_path, exist_ok=True)
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            safe_extractall(zf, dest_path)

        # Write sidecar: provenance (where/who/terms) for checkin routing
        from .auth import get_registry_url as _public_url
        source_meta = {
            "owner": owner,
            "registry_url": registry_url or _public_url(),
        }
        if governance:
            source_meta["governance"] = governance
        with open(os.path.join(dest_path, ".cartograph_source"), "w", encoding="utf-8") as f:
            json.dump(source_meta, f)

        # Adopt the publish-time validation stamp when the registry sent one
        # and the extracted bytes fingerprint-match it. Failure to adopt is
        # not an install failure - the widget just stays unstamped.
        from .validation_stamp import adopt_registry_stamp
        adopt_registry_stamp(dest_path, result.get("stamp"))

        return {
            "status": "success",
            "widget_id": widget_id,
            "version": version,
            "installed_at": dest_path,
            "source": "cloud",
        }
    except Exception as e:
        # Clean up partial install
        robust_rmtree(dest_path, ignore_errors=True)
        return _long_path_error(e, dest_path) or {"error": f"Failed to extract widget: {e}"}


def install(carto, widget_id, target_dir, version=None,
            _owner_hint=None, _registry_url=None):
    """Install a widget into target_dir/cg/<widget_id>.

    If widget_id has a known registry prefix (e.g. cg-foo, myorg-foo), the
    install goes directly to that registry and skips the local library.
    Unprefixed IDs use the existing local-first then cloud-fallback behavior.

    _owner_hint and _registry_url are internal params used by upgrade() to
    re-install the correct owner's widget from the correct registry.
    """
    # Strip @owner/ prefix if present (cloud widget IDs are namespaced)
    owner_hint = _owner_hint
    if widget_id.startswith("@"):
        parts = widget_id[1:].split("/", 1)
        if len(parts) == 2:
            owner_hint, widget_id = parts
            # Require a registry prefix when owner is specified.
            # @owner/bare-name is ambiguous — no sidecar, no traceability.
            if _resolve_registry(widget_id) is None:
                return {
                    "error": (
                        f"Registry prefix required when specifying an owner. "
                        f"Use @{owner_hint}/cg-{widget_id} for the public registry "
                        f"or @{owner_hint}/<prefix>-{widget_id} for a configured registry."
                    )
                }

    if not os.path.isabs(target_dir):
        return {"error": f"Target must be an absolute path, got: '{target_dir}'"}

    from .engine import PACKAGE_DIR
    if target_dir == os.path.abspath(carto.library_path):
        return {"error": f"Cannot install into the widget library ({carto.library_path})."}
    if os.path.abspath(target_dir) == os.path.dirname(PACKAGE_DIR):
        return {"error": f"Cannot install into the engine source directory ({os.path.dirname(PACKAGE_DIR)})."}

    dest_path = _widget_dir(target_dir, widget_id)

    if os.path.exists(dest_path):
        return {"error": f"'{widget_id}' already installed at {dest_path}. Uninstall first to reinstall."}

    # Explicit registry prefix: skip local library, go directly to that registry.
    # dest_path keeps the prefixed name (cg/cg-widget-name/) so prefixed and
    # local installs coexist without collision.
    resolved = _resolve_registry(widget_id)
    if resolved is not None:
        registry_url, _prefix, bare_id = resolved
        from .config import cloud_enabled, _PUBLIC_REGISTRY_URL
        if not cloud_enabled():
            return {"error": "Cloud is disabled. Enable it with: cartograph config cloud true"}

        # If the local library has this widget and its sidecar points to the
        # same registry, install from local - no cloud call needed.
        local_widget = next((w for w in carto.widgets if w["id"] == bare_id), None)
        if local_widget:
            try:
                with open(os.path.join(local_widget["path"], ".cartograph_source"), encoding="utf-8") as f:
                    sidecar = json.load(f)
                sidecar_reg = (sidecar.get("registry_url") or _PUBLIC_REGISTRY_URL).rstrip("/")
                target_reg = (registry_url or _PUBLIC_REGISTRY_URL).rstrip("/")
                if sidecar_reg == target_reg:
                    _copy_widget(local_widget["path"], dest_path)
                    carto._increment_install_count(bare_id)
                    return {
                        "status": "success",
                        "widget_id": widget_id,
                        "version": local_widget.get("version", "unknown"),
                        "installed_at": dest_path,
                        "source": "local",
                    }
            except Exception:
                pass  # no sidecar or mismatch - fall through to cloud

        result = _install_from_cloud(bare_id, dest_path, registry_url=registry_url,
                                    owner_hint=owner_hint)
        # Report back the prefixed widget_id (cg-widget-name), not the bare id
        if result.get("status") == "success":
            result["widget_id"] = widget_id
        return result

    # No prefix: try local library first
    widget = next((w for w in carto.widgets if w["id"] == widget_id), None)
    if widget:
        source_path = widget["path"]
        installed_version = widget.get("version", "0.0.0")

        if version and version != installed_version:
            history_path = os.path.join(source_path, "history", version)
            if not os.path.exists(history_path):
                return {"error": f"Version '{version}' not found for '{widget_id}'."}
            source_path = history_path
            installed_version = version

        try:
            _copy_widget(source_path, dest_path)
            carto._increment_install_count(widget_id)
            return {
                "status": "success",
                "widget_id": widget_id,
                "version": installed_version,
                "installed_at": dest_path,
            }
        except Exception as e:
            return _long_path_error(e, dest_path) or {"error": str(e)}

    # Fall back to cloud registry if enabled
    from .config import cloud_enabled
    if not cloud_enabled():
        return {"error": f"Widget '{widget_id}' not found in local library."}
    return _install_from_cloud(widget_id, dest_path, registry_url=_registry_url,
                               owner_hint=owner_hint)


def _upgrade_backup_path(widget_id: str) -> str:
    """Return the path to the single-slot upgrade backup for widget_id."""
    from .engine import _user_data_dir
    return os.path.join(_user_data_dir(), "upgrade-backup", widget_id)


def upgrade(carto, widget_id, target_dir, version=None):
    """Upgrade an installed widget to the latest (or specific) version.

    Backs up the current installation before removing it. If the new install
    fails, the backup is restored so the widget is never left in a broken state.
    """
    dest_path = _widget_dir(target_dir, widget_id)
    if not os.path.exists(dest_path):
        return {"error": f"'{widget_id}' not found at {dest_path}. Install it first."}

    # Read current version and sidecar before removing
    old_version = "unknown"
    try:
        with open(os.path.join(dest_path, "widget.json"), encoding="utf-8") as f:
            old_version = json.load(f).get("meta", {}).get("version", "unknown")
    except Exception:
        pass

    source_meta = {}
    try:
        with open(os.path.join(dest_path, ".cartograph_source"), encoding="utf-8") as f:
            source_meta = json.load(f)
    except Exception:
        pass

    # Back up current installation to a single-slot holding area
    backup_path = _upgrade_backup_path(widget_id)
    try:
        if os.path.exists(backup_path):
            robust_rmtree(backup_path)
        shutil.copytree(dest_path, backup_path)
    except Exception as e:
        return {"error": f"Could not create upgrade backup: {e}"}

    # Remove old copy
    result = uninstall(carto, widget_id, target_dir)
    if "error" in result:
        robust_rmtree(backup_path, ignore_errors=True)
        return result

    # Install new copy — pass owner and registry from sidecar so we upgrade
    # the correct owner's widget, not just the highest-ranked search result
    owner_hint = source_meta.get("owner") or None
    registry_url = source_meta.get("registry_url") or None
    result = install(carto, widget_id, target_dir, version=version,
                     _owner_hint=owner_hint, _registry_url=registry_url)
    if "error" in result:
        # Restore from backup
        try:
            shutil.copytree(backup_path, dest_path)
        except Exception as restore_err:
            result["restore_error"] = str(restore_err)
            result["backup_path"] = backup_path
        else:
            result["restored"] = True
            result["restored_version"] = old_version
        robust_rmtree(backup_path, ignore_errors=True)
        return result

    # Success — clean up backup
    robust_rmtree(backup_path, ignore_errors=True)
    result["previous_version"] = old_version
    return result


def delete_from_library(carto, widget_id, confirm=False):
    """Permanently remove a widget from the library."""
    widget = next((w for w in carto.widgets if w["id"] == widget_id), None)
    if not widget:
        return {"error": f"Widget '{widget_id}' not found. Use 'cartograph search' to find available widgets."}

    install_count = widget.get("install_count", 0)

    if not confirm:
        return {
            "status": "dry_run",
            "widget_id": widget_id,
            "version": widget.get("version", "unknown"),
            "install_count": install_count,
            "library_path": widget["path"],
            "message": "No changes made. Re-run with --confirm to permanently delete.",
        }

    widget_path = widget["path"]
    from .safefs import widget_lock, LockTimeout
    try:
        with widget_lock(carto.library_path, widget_id):
            robust_rmtree(widget_path)
    except LockTimeout as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Failed to delete: {e}"}

    if widget_id in carto.install_stats:
        del carto.install_stats[widget_id]
        carto._save_install_stats()

    return {
        "status": "success",
        "widget_id": widget_id,
        "deleted_from": widget_path,
        "install_count_at_deletion": install_count,
    }


def uninstall(carto, widget_id, target_dir):
    """Remove an installed widget from target_dir/cg/widget_id."""
    if not os.path.isabs(target_dir):
        return {"error": f"Target must be an absolute path, got: '{target_dir}'"}

    widget_path = _widget_dir(target_dir, widget_id)

    if not os.path.exists(widget_path):
        # Fall back to scanning cg/ for a prefixed install of the same
        # canonical id (e.g. cg-foo-python under cg/cg_foo_python/).
        from .engine import find_installed_widget_dir, DEFAULT_INSTALL_DIR
        cg_root = os.path.join(target_dir, DEFAULT_INSTALL_DIR)
        found = find_installed_widget_dir(cg_root, widget_id)
        if found is None:
            return {"error": f"'{widget_id}' not found at {widget_path}."}
        widget_path = found

    # Safety: must be inside the target dir
    if not os.path.abspath(widget_path).startswith(os.path.abspath(target_dir)):
        return {"error": f"Safety check failed: path escapes target directory."}

    try:
        robust_rmtree(widget_path)
        return {"status": "success", "widget_id": widget_id, "removed_from": widget_path}
    except Exception as e:
        return {"error": f"Failed to remove: {e}"}


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------

def rename_widget(carto, old_id: str, new_name: str | None,
                  new_domain: str | None, target_dir: str) -> dict:
    """Rename a scaffolded widget's slug and/or domain. Pre-checkin only.

    Identifiers are permanent once a widget is checked into the library —
    consumers elsewhere install by id, and renaming would silently orphan
    them. This command is strictly for the "scaffolded the wrong name,
    haven't checked in yet" case. It only touches ./cg/<old-dirname>/ in
    `target_dir`; library and cloud are never involved.

    Language is immutable (cross-language port = new widget via `create`).
    meta.name (display name) is left alone — it's author-owned.
    """
    from .engine import python_dir_name, DEFAULT_INSTALL_DIR, normalize_widget_id
    from .validator import VALID_DOMAINS

    if new_name is None and new_domain is None:
        return {"status": "error",
                "message": "Provide --name, --domain, or both."}

    old_id = normalize_widget_id(old_id)

    # Hard refusal: post-checkin widgets have a permanent id.
    if any(w["id"] == old_id for w in carto.widgets):
        return {"status": "error",
                "message": (f"'{old_id}' is already in the local library. Identifiers "
                            f"are permanent after checkin — consumers install by id and "
                            f"a rename would orphan them. Registry-level alias support "
                            f"is planned; for now, delete + recreate if this widget has "
                            f"no downstream users.")}

    if not os.path.isabs(target_dir):
        target_dir = os.path.abspath(target_dir)
    old_dir = os.path.join(target_dir, DEFAULT_INSTALL_DIR, python_dir_name(old_id))
    if not os.path.isdir(old_dir):
        return {"status": "error",
                "message": (f"Scaffolded widget not found at {old_dir}. `rename` "
                            f"operates on an un-checked-in scaffold in <target>/cg/.")}

    manifest_path = os.path.join(old_dir, "widget.json")
    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"status": "error", "message": f"Could not read widget.json: {e}"}

    language = (data.get("tech_stack", {}).get("language") or "").lower()
    if isinstance(data.get("tech_stack", {}).get("language"), list):
        language = data["tech_stack"]["language"][0].lower()
    if language != "python":
        return {"status": "error",
                "message": (f"Rename is currently supported only for Python widgets "
                            f"(got '{language}'). Non-Python widgets use different file "
                            f"layouts and need per-language support.")}

    old_domain = data.get("meta", {}).get("domain", "")
    suffix = f"-{language}"
    prefix = f"{old_domain}-"
    if not (old_id.startswith(prefix) and old_id.endswith(suffix)):
        return {"status": "error",
                "message": (f"Could not parse '{old_id}' as "
                            f"<{old_domain}>-<slug>-<{language}>.")}
    old_slug = old_id[len(prefix):-len(suffix)]

    new_slug = new_name if new_name is not None else old_slug
    new_dom = new_domain if new_domain is not None else old_domain

    if new_dom not in VALID_DOMAINS:
        return {"status": "error",
                "message": f"Unknown domain '{new_dom}'. Valid: {sorted(VALID_DOMAINS)}"}

    # The slug becomes a code identifier; reject names that can't be legal
    # (e.g. leading digit) before moving anything on disk - same rule as create.
    from cartograph.scaffolding import validate_name_identifier
    name_error = validate_name_identifier(new_slug, kind="widget")
    if name_error:
        return {"status": "error", "message": name_error}

    new_id = f"{new_dom}-{new_slug}-{language}"
    if new_id == old_id:
        return {"status": "error",
                "message": "New id matches the old id; nothing to rename."}

    # Also block if the new id collides with anything already in the library.
    if any(w["id"] == new_id for w in carto.widgets):
        return {"status": "error",
                "message": f"Widget '{new_id}' is already in the library."}

    new_dir = os.path.join(target_dir, DEFAULT_INSTALL_DIR, python_dir_name(new_id))
    if os.path.exists(new_dir):
        return {"status": "error",
                "message": f"Directory already exists: {new_dir}"}

    old_module = old_slug.replace("-", "_")
    new_module = new_slug.replace("-", "_")

    shutil.move(old_dir, new_dir)
    _rewrite_widget_dir(new_dir, new_id, new_dom, old_module, new_module)

    return {
        "status": "success",
        "old_id": old_id,
        "new_id": new_id,
        "path": new_dir,
    }


def _rewrite_widget_dir(path: str, new_id: str, new_domain: str,
                        old_module: str, new_module: str) -> None:
    """Update widget.json and rename Python module files + imports in-place."""
    manifest_path = os.path.join(path, "widget.json")
    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)
    meta = data.setdefault("meta", {})
    meta["id"] = new_id
    meta["domain"] = new_domain
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # Invalidate validation + dep-cache stamps — contents have changed.
    for _stamp_name in (".validation_stamp.json", ".dep_cache.json"):
        stamp = os.path.join(path, _stamp_name)
        if os.path.isfile(stamp):
            os.remove(stamp)

    if old_module != new_module:
        _rename_python_module(path, old_module, new_module)


def _rename_python_module(path: str, old_module: str, new_module: str) -> None:
    """Rename src/<old>.py, tests/test_<old>.py, and update imports across
    __init__.py, the renamed test file, and examples/example_usage.py."""
    src_old = os.path.join(path, "src", f"{old_module}.py")
    src_new = os.path.join(path, "src", f"{new_module}.py")
    if os.path.isfile(src_old):
        shutil.move(src_old, src_new)

    test_old = os.path.join(path, "tests", f"test_{old_module}.py")
    test_new = os.path.join(path, "tests", f"test_{new_module}.py")
    if os.path.isfile(test_old):
        shutil.move(test_old, test_new)

    # Rewrite imports. Matches:
    #   from .<old_module> import ...
    #   from src.<old_module> import ...
    #   import src.<old_module>
    # Doesn't touch `from src import X` (that still works after module rename
    # as long as __init__.py re-exports the same symbols).
    from_rel = re.compile(rf"(\bfrom\s+\.){re.escape(old_module)}(\b)")
    from_abs = re.compile(rf"(\bfrom\s+src\.){re.escape(old_module)}(\b)")
    import_abs = re.compile(rf"(\bimport\s+src\.){re.escape(old_module)}(\b)")

    for fpath in (
        os.path.join(path, "src", "__init__.py"),
        test_new,
        os.path.join(path, "examples", "example_usage.py"),
    ):
        if not os.path.isfile(fpath):
            continue
        with open(fpath, encoding="utf-8") as f:
            text = f.read()
        new_text = from_rel.sub(rf"\1{new_module}\2", text)
        new_text = from_abs.sub(rf"\1{new_module}\2", new_text)
        new_text = import_abs.sub(rf"\1{new_module}\2", new_text)
        if new_text != text:
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(new_text)
