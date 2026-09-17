"""cartograph import: hostile-zip handling.

Covers the Windows bug-hunt findings: backslash-separator zips (PowerShell
5.1 Compress-Archive), OS-metadata entries (__MACOSX/, AppleDouble ._*,
Thumbs.db), mixed-case widget dirs that would merge into an existing entry
on a case-insensitive filesystem, and validation artifacts left inside the
library entry by import's re-validation gate.
"""
import json
import os
import zipfile
from types import SimpleNamespace

import pytest

from tests.conftest import _make_widget

_SRC = '''def add(a, b):
    """Add two numbers."""
    return a + b
'''

_TEST = '''import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from adder import add


def test_add():
    assert add(1, 2) == 3
'''

_EXAMPLE = '''import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from adder import add

result = add(2, 2)
'''


def _build_widget(base, widget_id="universal-adder-python"):
    return _make_widget(
        base, widget_id, "Adder", "1.0.0", "universal", "python",
        ["math"], "Adds numbers", [], "adder.py", _SRC, _TEST, _EXAMPLE,
    )


def _zip_dir(src_root, zip_path, name_fn=lambda n: n, extra=()):
    with zipfile.ZipFile(zip_path, "w") as zf:
        for root, _dirs, files in os.walk(src_root):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, src_root).replace(os.sep, "/")
                with open(full, "rb") as fh:
                    zf.writestr(name_fn(rel), fh.read())
        for name, data in extra:
            zf.writestr(name, data)


@pytest.fixture
def import_env(tmp_path, monkeypatch):
    """Point the CLI's library at a fresh temp dir and return paths."""
    lib = tmp_path / "Widget_Library"
    lib.mkdir()
    import cartograph.engine as engine
    monkeypatch.setattr(engine, "LIBRARY_PATH", str(lib))
    staging = tmp_path / "staging"
    staging.mkdir()
    return SimpleNamespace(lib=str(lib), staging=str(staging),
                           tmp=str(tmp_path))


def _run_import(zip_path, force=False):
    from cartograph.cli import cmd_import
    return cmd_import(SimpleNamespace(path=str(zip_path), force=force))


def test_import_accepts_forward_slash_zip(import_env):
    _build_widget(import_env.staging)
    zp = os.path.join(import_env.tmp, "lib.zip")
    _zip_dir(import_env.staging, zp)
    _run_import(zp)
    dest = os.path.join(import_env.lib, "universal-adder-python")
    assert os.path.isfile(os.path.join(dest, "widget.json"))
    assert os.path.isfile(os.path.join(dest, "src", "adder.py"))


def test_import_normalizes_backslash_zip(import_env):
    """Compress-Archive-style zips use backslash separators; they must land
    as nested dirs on every OS, not as literal-backslash filenames."""
    _build_widget(import_env.staging)
    zp = os.path.join(import_env.tmp, "bs.zip")
    _zip_dir(import_env.staging, zp, name_fn=lambda n: n.replace("/", "\\"))
    _run_import(zp)
    dest = os.path.join(import_env.lib, "universal-adder-python")
    assert os.path.isfile(os.path.join(dest, "src", "adder.py"))
    # No literal-backslash spill anywhere in the library
    assert not [n for n in os.listdir(import_env.lib) if "\\" in n]


def test_import_skips_os_metadata_entries(import_env):
    """__MACOSX/, AppleDouble ._*, Thumbs.db and .DS_Store never reach the
    library - neither as phantom top-level dirs nor inside the widget."""
    _build_widget(import_env.staging)
    zp = os.path.join(import_env.tmp, "junk.zip")
    junk = [
        ("__MACOSX/universal-adder-python/._widget.json", b"\x00\x05\x16\x07"),
        ("universal-adder-python/src/._adder.py", b"\x00\x05\x16\x07"),
        ("universal-adder-python/Thumbs.db", b"junk"),
        ("universal-adder-python/.DS_Store", b"\x00junk"),
    ]
    _zip_dir(import_env.staging, zp, extra=junk)
    _run_import(zp)
    dest = os.path.join(import_env.lib, "universal-adder-python")
    assert os.path.isfile(os.path.join(dest, "src", "adder.py"))
    assert not os.path.exists(os.path.join(import_env.lib, "__MACOSX"))
    assert not os.path.exists(os.path.join(dest, "Thumbs.db"))
    assert not os.path.exists(os.path.join(dest, ".DS_Store"))
    assert not os.path.exists(os.path.join(dest, "src", "._adder.py"))
    # And no lock file was minted for the junk dir
    locks = os.listdir(os.path.join(import_env.lib, ".locks"))
    assert not [l for l in locks if "__MACOSX" in l]


def test_import_rejects_mixed_case_widget_dir(import_env, capsys):
    """A widget dir that isn't a lowercase slug is rejected: on NTFS/APFS it
    would silently merge into (and effectively rename) an existing entry."""
    _build_widget(import_env.staging)
    zp = os.path.join(import_env.tmp, "case.zip")
    _zip_dir(import_env.staging, zp,
             name_fn=lambda n: n.replace("adder", "ADDER"))
    _run_import(zp)
    out = capsys.readouterr().out
    assert "lowercase" in out
    assert not os.path.exists(
        os.path.join(import_env.lib, "universal-ADDER-python"))


def test_import_rejects_mixed_case_alongside_valid(import_env, capsys):
    _build_widget(import_env.staging)
    zp = os.path.join(import_env.tmp, "case2.zip")
    with zipfile.ZipFile(zp, "w") as zf:
        for root, _dirs, files in os.walk(import_env.staging):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, import_env.staging).replace(os.sep, "/")
                with open(full, "rb") as fh:
                    data = fh.read()
                zf.writestr(rel, data)
                zf.writestr(rel.replace("adder", "ADDER"), data)
    _run_import(zp)
    out = capsys.readouterr().out
    assert os.path.isfile(os.path.join(
        import_env.lib, "universal-adder-python", "src", "adder.py"))
    # Exact-name check: os.path.exists would match the lowercase dir on a
    # case-insensitive filesystem.
    assert "universal-ADDER-python" not in os.listdir(import_env.lib)
    assert "lowercase" in out


def test_clean_validation_artifacts(tmp_path):
    """Import's in-place re-validation must not leave .venv/.coverage/dep
    cache/__pycache__ inside the library entry."""
    from cartograph.cli import _clean_validation_artifacts
    w = tmp_path / "widget"
    (w / ".venv" / "Lib").mkdir(parents=True)
    (w / ".venv" / "Lib" / "site.py").write_text("x")
    (w / "src" / "__pycache__").mkdir(parents=True)
    (w / "src" / "__pycache__" / "m.pyc").write_text("x")
    (w / "src" / "mod.py").write_text("def f():\n    return 1\n")
    (w / ".coverage").write_text("data")
    (w / ".dep_cache.json").write_text("{}")
    (w / "widget.json").write_text("{}")
    _clean_validation_artifacts(str(w), "python")
    assert not (w / ".venv").exists()
    assert not (w / ".coverage").exists()
    assert not (w / ".dep_cache.json").exists()
    assert not (w / "src" / "__pycache__").exists()
    assert (w / "src" / "mod.py").exists()
    assert (w / "widget.json").exists()
