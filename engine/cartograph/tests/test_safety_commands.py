"""Regression tests for safety wiring on library-mutating commands.

These exercise the failure modes the safety audit flagged: zip-slip on
extraction, and tearing down the live library copy before the replacement is
ready. They target the seams (safefs primitives as used by sync/import) rather
than driving the full CLI, which needs cloud/registry plumbing.
"""

import io
import os
import zipfile
from io import BytesIO

import pytest

from cartograph.safefs import (
    safe_extractall,
    staged_dir,
    assert_members_safe,
    UnsafeArchiveError,
)


def _zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    buf.seek(0)
    return buf


# --- import: zip-slip guard on the member list -----------------------------

def test_import_member_guard_rejects_traversal(tmp_path):
    """The guard `import` runs up front rejects a library-escaping member."""
    names = ["widget-a/widget.json", "../../evil.py", "widget-a/src/m.py"]
    with pytest.raises(UnsafeArchiveError):
        assert_members_safe(names, str(tmp_path / "library"))


def test_import_member_guard_passes_normal_layout(tmp_path):
    names = ["w/widget.json", "w/src/m.py", "w/tests/t.py", "w/"]
    assert_members_safe(names, str(tmp_path / "library"))


# --- sync pull: staged safe extract --------------------------------------

def test_sync_pull_rejects_zip_slip_and_keeps_old_copy(tmp_path):
    """Modelled on the sync-pull path: extract registry bytes into a staged dir
    then atomic-swap. A zip-slip member must abort with the OLD library copy
    still intact - never the rmtree-first-then-fail behavior."""
    dest = tmp_path / "library" / "backend-evil-python"
    dest.mkdir(parents=True)
    (dest / "widget.json").write_text('{"old": true}')

    evil_bytes = _zip({"../../escaped.py": "pwned", "src/m.py": "x"}).getvalue()

    with pytest.raises(UnsafeArchiveError):
        with staged_dir(str(dest)) as staging:
            with zipfile.ZipFile(BytesIO(evil_bytes)) as zf:
                safe_extractall(zf, staging)

    # Old copy untouched; nothing escaped the library dir.
    assert (dest / "widget.json").read_text() == '{"old": true}'
    assert not (tmp_path / "escaped.py").exists()
    assert not any(p.name.startswith(".cg-new-")
                   for p in (tmp_path / "library").iterdir())


def test_sync_pull_swaps_in_clean_archive(tmp_path):
    """A well-formed pull fully replaces the old library copy atomically."""
    dest = tmp_path / "library" / "backend-ok-python"
    dest.mkdir(parents=True)
    (dest / "widget.json").write_text('{"version": "1.0.0"}')
    (dest / "stale.txt").write_text("remove me")

    good = _zip({
        "widget.json": '{"version": "2.0.0"}',
        "src/m.py": "def f(): pass",
    }).getvalue()

    with staged_dir(str(dest)) as staging:
        with zipfile.ZipFile(BytesIO(good)) as zf:
            safe_extractall(zf, staging)

    assert (dest / "widget.json").read_text() == '{"version": "2.0.0"}'
    assert (dest / "src" / "m.py").exists()
    assert not (dest / "stale.txt").exists()  # fully replaced, not merged
