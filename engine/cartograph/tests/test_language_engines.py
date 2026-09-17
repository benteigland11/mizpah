"""
Tests for the language engine registry and individual engines.
"""
import pytest
from cartograph.languages import get_engine, supported_languages
from cartograph.languages.base import LanguageEngine


def test_registry_returns_engine_for_python():
    engine = get_engine("python")
    assert engine is not None
    assert isinstance(engine, LanguageEngine)


def test_registry_case_insensitive():
    assert get_engine("Python") is get_engine("python")
    assert get_engine("PYTHON") is get_engine("python")


def test_registry_aliases():
    assert get_engine("js") is get_engine("javascript")
    assert get_engine("ts") is get_engine("typescript")
    assert get_engine("sv") is get_engine("systemverilog")
    assert get_engine("verilog") is get_engine("systemverilog")


def test_registry_unknown_returns_none():
    assert get_engine("brainfuck") is None
    assert get_engine("") is None


def test_supported_languages():
    langs = supported_languages()
    assert "python" in langs
    assert "javascript" in langs
    assert "nim" in langs
    assert "systemverilog" in langs


def test_allowed_extensions_includes_source_exts():
    from cartograph.languages.registry import allowed_extensions
    exts = allowed_extensions()
    assert "py" in exts
    assert "java" in exts


def test_allowed_extensions_includes_manifest_exts():
    # Engine-owned build files (Java's build.gradle) must be accepted by
    # the cloud zip validator, which builds its whitelist from this seam.
    from cartograph.languages.registry import allowed_extensions
    assert "gradle" in allowed_extensions()


def test_python_engine_run_tests_pass(tmp_path):
    from cartograph.languages.python import PythonEngine
    # Create a minimal passing widget
    src = tmp_path / "src"
    src.mkdir()
    (src / "widget.py").write_text("def hello(): return 'hello'\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_widget.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))\n"
        "from widget import hello\n"
        "def test_hello():\n"
        "    assert hello() == 'hello'\n"
    )
    engine = PythonEngine()
    result = engine.run_tests(str(tmp_path))
    assert result["passed"] is True


def test_python_engine_run_tests_fail(tmp_path):
    from cartograph.languages.python import PythonEngine
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_broken.py").write_text(
        "def test_fail():\n"
        "    assert False, 'intentional failure'\n"
    )
    engine = PythonEngine()
    result = engine.run_tests(str(tmp_path))
    assert result["passed"] is False
    assert "error" in result


def test_python_engine_no_tests(tmp_path):
    from cartograph.languages.python import PythonEngine
    (tmp_path / "tests").mkdir()
    engine = PythonEngine()
    result = engine.run_tests(str(tmp_path))
    assert result["passed"] is False


def test_engine_has_required_interface():
    """Every registered engine must implement install_deps and run_tests."""
    for lang in supported_languages():
        engine = get_engine(lang)
        assert hasattr(engine, "install_deps"), f"{lang} missing install_deps"
        assert hasattr(engine, "run_tests"), f"{lang} missing run_tests"
        assert callable(engine.install_deps)
        assert callable(engine.run_tests)


def test_python_runtime_version():
    import sys
    engine = get_engine("python")
    rv = engine.runtime_version()
    assert rv is not None
    assert rv.startswith("python ")
    expected = f"python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    assert rv == expected


def test_js_runtime_version():
    engine = get_engine("javascript")
    available, _ = engine.check_available()
    if not available:
        pytest.skip("node not installed")
    rv = engine.runtime_version()
    assert rv is not None
    assert rv.startswith("node ")


def test_nim_runtime_version():
    engine = get_engine("nim")
    available, _ = engine.check_available()
    if not available:
        pytest.skip("nim not installed")
    rv = engine.runtime_version()
    assert rv is not None
    assert rv.startswith("nim ")


@pytest.mark.systemverilog
def test_sv_runtime_version():
    engine = get_engine("systemverilog")
    available, _ = engine.check_available()
    if not available:
        pytest.skip("iverilog not installed")
    rv = engine.runtime_version()
    assert rv is not None
    assert "Icarus Verilog" in rv or "iverilog" in rv.lower()


def test_base_engine_runtime_version_is_none():
    engine = LanguageEngine()
    assert engine.runtime_version() is None


# ---------------------------------------------------------------------------
# OpenSCAD binary resolution
# ---------------------------------------------------------------------------
# Regression: on Windows the OpenSCAD installer does not add openscad to PATH,
# so shutil.which returns None even though the binary exists at the standard
# install location. The resolver must fall back to those known paths.

@pytest.mark.openscad
def test_openscad_resolver_uses_path(monkeypatch):
    from cartograph.languages import openscad as scad_mod
    monkeypatch.setattr("cartograph.config.get_path_override",
                        lambda name: None)  # hermetic vs resident config
    monkeypatch.setattr(scad_mod.shutil, "which",
                        lambda name: "/usr/bin/openscad" if name == "openscad" else None)
    monkeypatch.delenv("OPENSCAD_BINARY", raising=False)
    assert scad_mod._resolve_openscad_binary() == "/usr/bin/openscad"


@pytest.mark.openscad
def test_openscad_resolver_returns_none_when_missing(monkeypatch):
    from cartograph.languages import openscad as scad_mod
    monkeypatch.setattr(scad_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(scad_mod.os.path, "isfile", lambda p: False)
    monkeypatch.delenv("OPENSCAD_BINARY", raising=False)
    assert scad_mod._resolve_openscad_binary() is None


@pytest.mark.openscad
def test_openscad_resolver_windows_install_dir_fallback(monkeypatch):
    """Windows installer puts openscad.exe in C:\\Program Files\\OpenSCAD but
    does not add it to PATH. shutil.which returns None; resolver must still find it."""
    from cartograph.languages import openscad as scad_mod
    monkeypatch.setattr(scad_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(scad_mod.os, "name", "nt")
    expected = r"C:\Program Files\OpenSCAD\openscad.exe"
    monkeypatch.setattr(scad_mod.os.path, "isfile", lambda p: p == expected)
    monkeypatch.delenv("OPENSCAD_BINARY", raising=False)
    assert scad_mod._resolve_openscad_binary() == expected


@pytest.mark.openscad
def test_openscad_resolver_honors_env_override(monkeypatch, tmp_path):
    """OPENSCAD_BINARY env var lets users point at a nonstandard install."""
    from cartograph.languages import openscad as scad_mod
    monkeypatch.setattr("cartograph.config.get_path_override",
                        lambda name: None)  # hermetic vs resident config
    fake = tmp_path / "openscad"
    fake.write_text("")
    monkeypatch.setattr(scad_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(scad_mod.os, "name", "posix")
    monkeypatch.setenv("OPENSCAD_BINARY", str(fake))
    assert scad_mod._resolve_openscad_binary() == str(fake)


@pytest.mark.openscad
def test_openscad_check_available_windows_hint(monkeypatch):
    """When openscad is missing on Windows, the error must mention the PATH issue."""
    from cartograph.languages import openscad as scad_mod
    monkeypatch.setattr(scad_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(scad_mod.os.path, "isfile", lambda p: False)
    monkeypatch.setattr(scad_mod.os, "name", "nt")
    monkeypatch.delenv("OPENSCAD_BINARY", raising=False)
    engine = scad_mod.OpenSCADEngine()
    # Bypass the per-instance cache for this test
    if hasattr(engine, "_openscad_bin_cache"):
        del engine._openscad_bin_cache
    ok, msg = engine.check_available()
    assert not ok
    assert "PATH" in msg or "OPENSCAD_BINARY" in msg


# ---------------------------------------------------------------------------
# Python sidecar (e.g. python/ inside an openscad widget)
# ---------------------------------------------------------------------------

def test_base_engine_sidecar_default_is_none():
    from cartograph.languages.base import LanguageEngine
    assert LanguageEngine().sidecar() is None


def test_python_engine_has_no_sidecar():
    assert get_engine("python").sidecar() is None


def test_openscad_engine_declares_python_sidecar():
    sc = get_engine("openscad").sidecar()
    assert sc == ("python", "python", 60)


def test_validate_subtree_passes_with_covered_calc(tmp_path):
    py_engine = get_engine("python")
    sub = tmp_path / "python"
    sub.mkdir()
    (sub / "threads.py").write_text(
        "def pitch(d):\n"
        "    if d <= 0:\n"
        "        return 0\n"
        "    return d * 0.1\n"
    )
    (sub / "test_threads.py").write_text(
        "from threads import pitch\n"
        "def test_positive():\n"
        "    assert pitch(10) == 1.0\n"
        "def test_nonpositive():\n"
        "    assert pitch(0) == 0\n"
    )
    result = py_engine.validate_subtree(str(tmp_path), "python", coverage=60)
    assert result["passed"], result.get("error")


def test_validate_subtree_fails_below_coverage(tmp_path):
    py_engine = get_engine("python")
    sub = tmp_path / "python"
    sub.mkdir()
    # Calc with two branches but tests cover only one — coverage will be low.
    (sub / "calc.py").write_text(
        "def half_or_zero(x):\n"
        "    if x > 100:\n"
        "        return x // 2\n"
        "    if x > 50:\n"
        "        return x // 3\n"
        "    if x > 25:\n"
        "        return x // 4\n"
        "    return 0\n"
    )
    (sub / "test_calc.py").write_text(
        "from calc import half_or_zero\n"
        "def test_zero():\n"
        "    assert half_or_zero(1) == 0\n"
    )
    result = py_engine.validate_subtree(str(tmp_path), "python", coverage=80)
    assert not result["passed"]
    assert "sidecar" in result["error"].lower()


def test_validate_subtree_blocks_print(tmp_path):
    py_engine = get_engine("python")
    sub = tmp_path / "python"
    sub.mkdir()
    (sub / "calc.py").write_text(
        "def calc(x):\n"
        "    print('debug')\n"
        "    return x\n"
    )
    (sub / "test_calc.py").write_text(
        "from calc import calc\n"
        "def test_calc():\n"
        "    assert calc(1) == 1\n"
    )
    result = py_engine.validate_subtree(str(tmp_path), "python", coverage=60)
    assert not result["passed"]
    assert "print()" in result["error"]


def test_validate_subtree_blocks_non_stdlib_import(tmp_path):
    py_engine = get_engine("python")
    sub = tmp_path / "python"
    sub.mkdir()
    (sub / "calc.py").write_text(
        "import requests\n"
        "def calc():\n"
        "    return requests\n"
    )
    (sub / "test_calc.py").write_text(
        "from calc import calc\n"
        "def test_calc():\n"
        "    assert calc() is not None\n"
    )
    result = py_engine.validate_subtree(str(tmp_path), "python", coverage=60)
    assert not result["passed"]
    assert "stdlib" in result["error"].lower()


def test_validate_subtree_requires_tests(tmp_path):
    py_engine = get_engine("python")
    sub = tmp_path / "python"
    sub.mkdir()
    (sub / "calc.py").write_text("def calc(): return 1\n")
    result = py_engine.validate_subtree(str(tmp_path), "python", coverage=60)
    assert not result["passed"]
    assert "test_*.py" in result["error"]


def test_validate_subtree_empty_dir_passes(tmp_path):
    """Empty / missing sidecar is silently OK — dispatcher decides whether to call."""
    py_engine = get_engine("python")
    # No python/ at all
    assert py_engine.validate_subtree(str(tmp_path), "python", coverage=60)["passed"]
    # Empty python/
    (tmp_path / "python").mkdir()
    assert py_engine.validate_subtree(str(tmp_path), "python", coverage=60)["passed"]


def test_openscad_scaffold_writes_python_sidecar(tmp_path):
    """OpenSCAD scaffold always creates python/<module>.py + python/test_<module>.py."""
    engine = get_engine("openscad")
    for d in ("src", "tests", "examples"):
        (tmp_path / d).mkdir()
    engine.scaffold(str(tmp_path), "threads", "Test Threads")
    py_dir = tmp_path / "python"
    assert py_dir.is_dir()
    assert (py_dir / "threads.py").exists()
    assert (py_dir / "test_threads.py").exists()
    # The scaffold stubs should self-validate at 60% coverage
    py_engine = get_engine("python")
    result = py_engine.validate_subtree(str(tmp_path), "python", coverage=60)
    assert result["passed"], result.get("error")


def test_openscad_watched_patterns_include_python_dir():
    engine = get_engine("openscad")
    patterns = engine.watched_patterns("/some/path")
    assert any("python" in p and "**" in p for p in patterns)


def test_engines_survive_src_namespace_shadowing(tmp_path):
    """All bundled engines must register even when another top-level `src`
    namespace package wins the name first (sloppy third-party wheels ship
    stray src/ dirs into site-packages; src-layout cwds do the same).

    Regression: openscad/systemverilog imported block_walker as a bare
    `from src...` after a sys.path insert, so a pre-existing `src` in
    sys.modules knocked both engines out of the registry silently.
    """
    import os
    import subprocess
    import sys

    decoy = tmp_path / "src"
    decoy.mkdir()  # no __init__.py -> namespace package, like the wild cases
    code = (
        "import sys; sys.path.insert(0, {decoy!r}); import src\n"
        "from cartograph.languages import supported_languages\n"
        "langs = supported_languages()\n"
        "missing = {{'openscad', 'systemverilog'}} - set(langs)\n"
        "assert not missing, f'engines lost to src shadowing: {{missing}}'\n"
    ).format(decoy=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env={**os.environ, "PYTHONPATH": "src" + os.pathsep + "."},
    )
    assert result.returncode == 0, result.stderr


def _write_go_widget(root, src_body):
    import os
    for sub in ("src", "tests", "examples"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    # newline="\n": Go source is canonically LF and gofmt flags CRLF, so on
    # Windows (where text mode would write \r\n) we must pin LF or the
    # "formatted" fixture would spuriously fail the gofmt gate.
    with open(os.path.join(root, "go.mod"), "w", newline="\n") as f:
        f.write("module probe\n\ngo 1.24\n")
    with open(os.path.join(root, "src", "probe.go"), "w", newline="\n") as f:
        f.write(src_body)


def test_go_gofmt_blocks_unformatted_source(tmp_path):
    """gofmt is a hard formatting floor - unformatted Go fails validation."""
    import shutil
    if shutil.which("go") is None or shutil.which("gofmt") is None:
        import pytest
        pytest.skip("go toolchain not installed")
    engine = get_engine("go")
    # Leading spaces instead of a tab - gofmt rewrites this.
    _write_go_widget(str(tmp_path),
        "// Package probe does things.\npackage probe\n\n"
        "// Run returns input.\nfunc Run(v string) string {\n    return v\n}\n")
    result = engine.validate_widget(str(tmp_path), [])
    assert not result["passed"]
    assert "gofmt" in result.get("error", "")


def test_go_gofmt_passes_formatted_source(tmp_path):
    import shutil
    if shutil.which("go") is None or shutil.which("gofmt") is None:
        import pytest
        pytest.skip("go toolchain not installed")
    engine = get_engine("go")
    _write_go_widget(str(tmp_path),
        "// Package probe does things.\npackage probe\n\n"
        "// Run returns input.\nfunc Run(v string) string {\n\treturn v\n}\n")
    result = engine.validate_widget(str(tmp_path), [])
    assert result["passed"], result.get("error")
