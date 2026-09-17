"""
Tests for install and uninstall.
"""
import os
import pytest


@pytest.fixture
def fresh_carto(fixture_library, tmp_path):
    """A fresh Cartograph instance with an isolated install dir."""
    from cartograph import Cartograph
    c = Cartograph(
        library_path=fixture_library,
    )
    return c, str(tmp_path)


def _widget_path(target, widget_id):
    """Widgets now live at <project_root>/cg/<widget_id>."""
    return os.path.join(target, "cg", widget_id)


def test_install_widget(fresh_carto):
    carto, target = fresh_carto
    result = carto.install("http-client", target)
    assert result.get("status") == "success"
    assert result["widget_id"] == "http-client"
    assert result["version"] == "1.2.0"
    assert os.path.isdir(result["installed_at"])


def test_install_unknown_widget(fresh_carto):
    carto, target = fresh_carto
    result = carto.install("no-such-widget-xyz", target)
    assert "error" in result


def test_install_creates_files(fresh_carto):
    carto, target = fresh_carto
    carto.install("json-parser", target)
    widget_dir = _widget_path(target, "json-parser")
    assert os.path.exists(os.path.join(widget_dir, "widget.json"))
    assert os.path.isdir(os.path.join(widget_dir, "src"))


def test_install_duplicate_blocked(fresh_carto):
    carto, target = fresh_carto
    carto.install("http-client", target)
    result = carto.install("http-client", target)
    assert "error" in result
    assert "already installed" in result["error"].lower()


def test_install_rejects_relative_path(fresh_carto):
    carto, _ = fresh_carto
    result = carto.install("http-client", "relative/path")
    assert "error" in result


def test_uninstall_widget(fresh_carto):
    carto, target = fresh_carto
    carto.install("http-client", target)
    result = carto.uninstall("http-client", target)
    assert result.get("status") == "success"
    assert not os.path.exists(_widget_path(target, "http-client"))


def test_uninstall_not_installed(fresh_carto):
    carto, target = fresh_carto
    result = carto.uninstall("http-client", target)
    assert "error" in result


# ---------------------------------------------------------------------------
# Language-specific file preservation on install
# ---------------------------------------------------------------------------

def test_install_python_has_init(fresh_carto):
    """Python widget install should include src/__init__.py."""
    carto, target = fresh_carto
    result = carto.install("http-client", target)
    assert result.get("status") == "success"
    widget_dir = result["installed_at"]
    assert os.path.exists(os.path.join(widget_dir, "src", "__init__.py"))


def test_install_nim_has_nimble(fresh_carto):
    """Nim widget install should include the .nimble file."""
    carto, target = fresh_carto
    result = carto.install("universal-add-nim", target)
    assert result.get("status") == "success"
    widget_dir = result["installed_at"]
    nimble_files = [f for f in os.listdir(widget_dir) if f.endswith(".nimble")]
    assert len(nimble_files) == 1, f"Expected 1 .nimble file, found: {nimble_files}"


def test_install_js_has_package_json(fresh_carto):
    """JS widget install should include package.json."""
    carto, target = fresh_carto
    result = carto.install("data-sum-javascript", target)
    assert result.get("status") == "success"
    widget_dir = result["installed_at"]
    assert os.path.exists(os.path.join(widget_dir, "package.json"))


# ---------------------------------------------------------------------------
# Prefixed install: commands use the prefixed id throughout
# ---------------------------------------------------------------------------

def test_uninstall_prefixed_id(fresh_carto, fixture_library):
    """Uninstalling a widget using its prefixed id should work.

    The installed dir name IS the widget_id — cg-http-client installs to
    cg/cg-http-client/ and is uninstalled the same way.
    """
    import json
    from cartograph import Cartograph
    from cartograph.installer import _widget_dir
    carto, target = fresh_carto

    # Simulate a prefixed install: the dir name matches the prefixed id
    prefixed_id = "cg-http-client"
    wdir = _widget_dir(target, prefixed_id)
    os.makedirs(os.path.join(wdir, "src"), exist_ok=True)
    with open(os.path.join(wdir, "widget.json"), "w") as f:
        json.dump({"meta": {"id": "http-client", "version": "1.0.0"}}, f)

    result = carto.uninstall(prefixed_id, target)
    assert result.get("status") == "success", result
    assert not os.path.isdir(wdir)


# ---------------------------------------------------------------------------
# Per-language project-file preservation in _copy_widget
# ---------------------------------------------------------------------------
# These language project files (manifests, build configs) are required for
# the runtime/build to find the widget. _copy_widget has an explicit allowlist
# — a regression dropping any of them silently breaks installs in that
# language. Tested directly against _copy_widget with a synthetic source so we
# don't need fixture widgets in every supported language.

import pytest as _pytest


@_pytest.mark.parametrize("filename", [
    "Cargo.toml", "Cargo.lock",
    "go.mod", "go.sum",
    "package.json", "package-lock.json", "tsconfig.json",
    "CMakeLists.txt", "Makefile",
    "pom.xml", "build.gradle",
])
def test_copy_widget_preserves_language_project_file(tmp_path, filename):
    from cartograph.installer import _copy_widget
    src = tmp_path / "src_widget"
    dst = tmp_path / "dst_widget"
    (src / "src").mkdir(parents=True)
    (src / "src" / "code.txt").write_text("hi")
    (src / filename).write_text(f"# {filename} content")

    _copy_widget(str(src), str(dst))

    copied = dst / filename
    assert copied.is_file(), f"{filename} missing from install"
    assert copied.read_text() == f"# {filename} content"


def test_copy_widget_preserves_csproj_glob(tmp_path):
    """*.csproj is matched by glob, not exact name."""
    from cartograph.installer import _copy_widget
    src = tmp_path / "src_widget"
    dst = tmp_path / "dst_widget"
    (src / "src").mkdir(parents=True)
    (src / "MyProject.csproj").write_text("<Project/>")
    (src / "OtherProject.csproj").write_text("<Project/>")

    _copy_widget(str(src), str(dst))

    assert (dst / "MyProject.csproj").is_file()
    assert (dst / "OtherProject.csproj").is_file()


def test_copy_widget_preserves_nimble_glob(tmp_path):
    """*.nimble is matched by glob (Nim package name varies per widget)."""
    from cartograph.installer import _copy_widget
    src = tmp_path / "src_widget"
    dst = tmp_path / "dst_widget"
    (src / "src").mkdir(parents=True)
    (src / "mywidget.nimble").write_text('version = "0.1.0"')

    _copy_widget(str(src), str(dst))

    assert (dst / "mywidget.nimble").is_file()


def test_copy_widget_skips_pycache(tmp_path):
    """__pycache__ and .pyc must be filtered, otherwise installs ship build
    artifacts from the library copy."""
    from cartograph.installer import _copy_widget
    src = tmp_path / "src_widget"
    dst = tmp_path / "dst_widget"
    (src / "src" / "__pycache__").mkdir(parents=True)
    (src / "src" / "__pycache__" / "stale.pyc").write_text("compiled")
    (src / "src" / "real.py").write_text("x = 1")

    _copy_widget(str(src), str(dst))

    assert (dst / "src" / "real.py").is_file()
    assert not (dst / "src" / "__pycache__").exists()


# ---------------------------------------------------------------------------
# Safety guards
# ---------------------------------------------------------------------------

def test_install_into_library_path_blocked(fresh_carto, fixture_library):
    """Installing into the library root would overwrite source widgets."""
    carto, _ = fresh_carto
    result = carto.install("http-client", os.path.abspath(fixture_library))
    assert "error" in result
    assert "library" in result["error"].lower()


def test_install_into_engine_dir_blocked(fresh_carto):
    """Installing into the engine source dir would clobber the CLI itself."""
    import os as _os
    from cartograph.engine import PACKAGE_DIR
    carto, _ = fresh_carto
    # PACKAGE_DIR is .../src/cartograph; the parent ('src') is what the guard checks.
    engine_parent = _os.path.dirname(PACKAGE_DIR)
    result = carto.install("http-client", engine_parent)
    assert "error" in result
    assert "engine" in result["error"].lower()


# ---------------------------------------------------------------------------
# Install count tracking
# ---------------------------------------------------------------------------

def test_install_increments_install_count(fresh_carto):
    """Each successful install bumps install_stats[id] by 1."""
    carto, target = fresh_carto
    starting = carto._get_install_count("http-client")
    carto.install("http-client", target)
    assert carto._get_install_count("http-client") == starting + 1


# ---------------------------------------------------------------------------
# Versioned install from history
# ---------------------------------------------------------------------------

def test_install_specific_version_from_history(fresh_carto, fixture_library):
    """`install --version X` resolves against history/<X>/ when present."""
    import json as _json
    carto, target = fresh_carto

    # Plant a synthetic history entry under http-client.
    hist = os.path.join(fixture_library, "http-client", "history", "0.9.0")
    os.makedirs(os.path.join(hist, "src"), exist_ok=True)
    with open(os.path.join(hist, "widget.json"), "w") as f:
        _json.dump({"meta": {"id": "http-client", "version": "0.9.0",
                             "domain": "backend", "tags": ["a"]},
                    "tech_stack": {"language": "python", "dependencies": []}}, f)
    with open(os.path.join(hist, "src", "marker.txt"), "w") as f:
        f.write("from history")

    try:
        result = carto.install("http-client", target, version="0.9.0")
        assert result.get("status") == "success", result
        assert result["version"] == "0.9.0"
        assert os.path.isfile(
            os.path.join(_widget_path(target, "http-client"), "src", "marker.txt"))
    finally:
        import shutil as _sh
        _sh.rmtree(os.path.join(fixture_library, "http-client", "history"),
                   ignore_errors=True)


def test_install_unknown_version_errors(fresh_carto):
    carto, target = fresh_carto
    result = carto.install("http-client", target, version="99.99.99")
    assert "error" in result
    assert "version" in result["error"].lower() and "99.99.99" in result["error"]


# ---------------------------------------------------------------------------
# Multi-registry prefixed install routing (Top-3 priority #1)
# ---------------------------------------------------------------------------

def _zip_widget_bytes(widget_id: str, version: str = "1.0.0"):
    """Build an in-memory widget zip the way the cloud download endpoint would."""
    import io
    import json as _json
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("widget.json", _json.dumps({
            "meta": {"id": widget_id, "version": version,
                     "domain": "backend", "tags": ["a", "b", "c"]},
            "tech_stack": {"language": "python", "dependencies": []},
        }))
        zf.writestr("src/__init__.py", "")
    return buf.getvalue()


def test_install_cg_prefixed_routes_to_cloud(fresh_carto):
    """`cg-foo` skips local even when an unrelated `foo` widget would match."""
    from unittest.mock import patch
    carto, target = fresh_carto
    with patch("cartograph.config.cloud_enabled", return_value=True), \
         patch("cartograph.cloud.search", return_value={
             "widgets": [{"id": "not-in-library", "owner": "alice"}]
         }), \
         patch("cartograph.cloud.download_widget") as mock_dl:
        mock_dl.return_value = {
            "zip_bytes": _zip_widget_bytes("not-in-library"),
            "version": "1.0.0",
            "governance": "open",
        }
        result = carto.install("cg-not-in-library", target)

    assert result.get("status") == "success", result
    assert result["widget_id"] == "cg-not-in-library"
    assert result["source"] == "cloud"
    # Sidecar was written with public registry url
    sidecar = os.path.join(_widget_path(target, "cg-not-in-library"),
                           ".cartograph_source")
    assert os.path.isfile(sidecar)
    import json
    with open(sidecar) as f:
        meta = json.load(f)
    assert meta["registry_url"] == "https://api.cartograph.tools"


def test_install_myorg_prefixed_routes_to_configured_registry(fresh_carto):
    """A user-configured registry prefix routes to that registry's URL."""
    from unittest.mock import patch
    carto, target = fresh_carto
    fake_registries = [{"prefix": "myorg", "url": "https://reg.myorg.test"}]
    with patch("cartograph.config.cloud_enabled", return_value=True), \
         patch("cartograph.config.get_registries", return_value=fake_registries), \
         patch("cartograph.cloud.search", return_value={
             "widgets": [{"id": "internal-tool", "owner": "alice"}]
         }), \
         patch("cartograph.cloud.download_widget") as mock_dl:
        mock_dl.return_value = {
            "zip_bytes": _zip_widget_bytes("internal-tool"),
            "version": "2.1.0",
        }
        result = carto.install("myorg-internal-tool", target)

    assert result.get("status") == "success", result
    # download_widget must be called with the myorg registry url, not public
    kwargs = mock_dl.call_args.kwargs
    assert kwargs.get("registry_url") == "https://reg.myorg.test"
    # And with the BARE id, not the prefixed one
    args = mock_dl.call_args.args
    assert "internal-tool" in args or kwargs.get("widget_id") == "internal-tool"


def test_install_prefixed_with_cloud_disabled_errors(fresh_carto):
    """Prefixed install must error clearly when cloud is disabled."""
    from unittest.mock import patch
    carto, target = fresh_carto
    with patch("cartograph.config.cloud_enabled", return_value=False):
        result = carto.install("cg-anything", target)
    assert "error" in result
    assert "cloud is disabled" in result["error"].lower()


def test_install_prefixed_uses_local_when_sidecar_matches(fresh_carto):
    """If local library has the bare widget AND its sidecar registry matches,
    install copies from local — no cloud call."""
    import json
    from unittest.mock import patch
    carto, target = fresh_carto

    # Plant a sidecar on the existing http-client library entry pointing to public.
    library_widget = next(w for w in carto.widgets if w["id"] == "http-client")
    sidecar_path = os.path.join(library_widget["path"], ".cartograph_source")
    with open(sidecar_path, "w") as f:
        json.dump({"owner": "alice",
                   "registry_url": "https://api.cartograph.tools"}, f)

    try:
        with patch("cartograph.config.cloud_enabled", return_value=True), \
             patch("cartograph.cloud.download_widget") as mock_dl:
            result = carto.install("cg-http-client", target)
        assert result.get("status") == "success", result
        assert result["source"] == "local"
        mock_dl.assert_not_called()
    finally:
        os.remove(sidecar_path)


def test_install_prefixed_falls_through_when_sidecar_mismatches(fresh_carto):
    """Local widget exists but sidecar points to a different registry — must
    NOT use the local copy; falls through to cloud."""
    import json
    from unittest.mock import patch
    carto, target = fresh_carto

    library_widget = next(w for w in carto.widgets if w["id"] == "http-client")
    sidecar_path = os.path.join(library_widget["path"], ".cartograph_source")
    with open(sidecar_path, "w") as f:
        # Sidecar says it came from a different registry than the cg- prefix targets.
        json.dump({"owner": "alice",
                   "registry_url": "https://reg.other.test"}, f)

    try:
        with patch("cartograph.config.cloud_enabled", return_value=True), \
             patch("cartograph.cloud.search", return_value={
                 "widgets": [{"id": "http-client", "owner": "alice"}]
             }), \
             patch("cartograph.cloud.download_widget") as mock_dl:
            mock_dl.return_value = {
                "zip_bytes": _zip_widget_bytes("http-client", "1.2.0"),
                "version": "1.2.0",
            }
            result = carto.install("cg-http-client", target)
        assert result.get("status") == "success", result
        assert result["source"] == "cloud"
        mock_dl.assert_called_once()
    finally:
        os.remove(sidecar_path)


def test_install_owner_prefixed_widget_requires_registry_prefix(fresh_carto):
    """`@alice/bare-name` is ambiguous (no registry traceability) and errors."""
    carto, target = fresh_carto
    result = carto.install("@alice/some-widget", target)
    assert "error" in result
    assert "registry prefix required" in result["error"].lower()


def test_install_unknown_prefix_falls_through_to_local(fresh_carto):
    """An id that just happens to start with a hyphenated word but matches no
    configured prefix is treated as a normal local-first lookup."""
    from unittest.mock import patch
    carto, target = fresh_carto
    # http-client starts with 'http-' but 'http' is not a registered prefix.
    with patch("cartograph.config.get_registries", return_value=[]):
        result = carto.install("http-client", target)
    assert result.get("status") == "success"
    assert result.get("source") != "cloud"


def test_status_prefixed_id(fresh_carto, fixture_library):
    """Status check with a prefixed id resolves the library entry via bare id."""
    import json
    from cartograph.installer import _widget_dir
    carto, target = fresh_carto

    prefixed_id = "cg-http-client"
    wdir = _widget_dir(target, prefixed_id)
    os.makedirs(os.path.join(wdir, "src"), exist_ok=True)
    with open(os.path.join(wdir, "widget.json"), "w") as f:
        json.dump({"meta": {"id": "http-client", "version": "1.2.0"}}, f)

    result = carto.widget_status(widget_id=prefixed_id, target_dir=target)
    assert "error" not in result, result
    assert result["widget_id"] == prefixed_id
