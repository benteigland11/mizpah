"""A nested `.terra/.terra` must never be mistaken for a project root.

A probe writing a RELATIVE `.terra/...` path from inside `.terra` creates one.
The naive parent-walk then resolves the project root to `<proj>/.terra`, and
every belief read after that silently hits the wrong tree with no error.
"""
from __future__ import annotations

from pathlib import Path

from terra.paths import find_project_root


def test_nested_terra_is_not_a_project_root(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    (proj / ".terra").mkdir(parents=True)
    stray = proj / ".terra" / ".terra" / "artifacts" / "leak_check"
    stray.mkdir(parents=True)

    # from inside the stray tree, the answer must still be the REAL project
    assert find_project_root(stray) == proj
    assert find_project_root(proj / ".terra") == proj
    assert find_project_root(proj / ".terra" / ".terra") == proj


def test_ordinary_resolution_unaffected(tmp_path: Path) -> None:
    """CAN-FAIL: the skip must not break the normal case."""
    proj = tmp_path / "proj"
    (proj / ".terra").mkdir(parents=True)
    deep = proj / "tools" / "cfd"
    deep.mkdir(parents=True)
    assert find_project_root(deep) == proj
    assert find_project_root(proj) == proj


def test_no_terra_anywhere_returns_none(tmp_path: Path) -> None:
    d = tmp_path / "nothing" / "here"
    d.mkdir(parents=True)
    assert find_project_root(d) is None
