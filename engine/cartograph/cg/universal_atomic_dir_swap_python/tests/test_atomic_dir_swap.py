import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.atomic_dir_swap import staged_dir, atomic_swap_dir


def test_staged_dir_swaps_in_on_success(tmp_path):
    dest = tmp_path / "widget"
    dest.mkdir()
    (dest / "old.txt").write_text("old")
    with staged_dir(str(dest)) as tmp:
        with open(os.path.join(tmp, "new.txt"), "w") as f:
            f.write("new")
    assert (dest / "new.txt").read_text() == "new"
    assert not (dest / "old.txt").exists()  # full replacement, not a merge


def test_staged_dir_creates_dest_when_absent(tmp_path):
    dest = tmp_path / "sub" / "widget"  # neither exists yet
    with staged_dir(str(dest)) as tmp:
        with open(os.path.join(tmp, "f.txt"), "w") as f:
            f.write("v")
    assert (dest / "f.txt").read_text() == "v"


def test_staged_dir_leaves_dest_intact_on_failure(tmp_path):
    dest = tmp_path / "widget"
    dest.mkdir()
    (dest / "keep.txt").write_text("important")
    with pytest.raises(RuntimeError):
        with staged_dir(str(dest)) as tmp:
            with open(os.path.join(tmp, "partial.txt"), "w") as f:
                f.write("half")
            raise RuntimeError("boom mid-build")
    assert (dest / "keep.txt").read_text() == "important"
    assert not (dest / "partial.txt").exists()
    # No staging crumbs left behind.
    assert not any(p.name.startswith(".cg-new-") for p in tmp_path.iterdir())


def test_atomic_swap_into_empty_slot(tmp_path):
    dest = tmp_path / "widget"  # does not exist yet
    new = tmp_path / ".cg-new-y"
    new.mkdir()
    (new / "f.txt").write_text("v")
    atomic_swap_dir(str(new), str(dest))
    assert (dest / "f.txt").read_text() == "v"
    assert not new.exists()  # consumed by the swap


def test_atomic_swap_replaces_existing(tmp_path):
    dest = tmp_path / "widget"
    dest.mkdir()
    (dest / "a.txt").write_text("a")
    new = tmp_path / ".cg-new-z"
    new.mkdir()
    (new / "b.txt").write_text("b")
    atomic_swap_dir(str(new), str(dest))
    assert (dest / "b.txt").read_text() == "b"
    assert not (dest / "a.txt").exists()


def test_atomic_swap_rolls_back_if_replace_fails(tmp_path, monkeypatch):
    dest = tmp_path / "widget"
    dest.mkdir()
    (dest / "keep.txt").write_text("original")

    new = tmp_path / ".cg-new-x"
    new.mkdir()
    (new / "new.txt").write_text("replacement")

    import src.atomic_dir_swap as mod
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(a, b):
        calls["n"] += 1
        if calls["n"] == 2:  # first moves dest aside; fail the second
            raise OSError("simulated swap failure")
        return real_replace(a, b)

    monkeypatch.setattr(mod.os, "replace", flaky_replace)
    with pytest.raises(OSError):
        atomic_swap_dir(str(new), str(dest))
    # Rolled back: original content restored, not destroyed.
    assert (dest / "keep.txt").read_text() == "original"


def test_no_leftover_backup_dirs(tmp_path):
    dest = tmp_path / "widget"
    dest.mkdir()
    (dest / "x").write_text("1")
    with staged_dir(str(dest)) as tmp:
        with open(os.path.join(tmp, "y"), "w") as f:
            f.write("2")
    assert not any(p.name.startswith(".cg-old-") for p in tmp_path.iterdir())


def _make_readonly_tree(tmp_path):
    tree = tmp_path / "victim"
    (tree / "sub").mkdir(parents=True)
    locked = tree / "sub" / "locked.txt"
    locked.write_text("keep out")
    os.chmod(locked, 0o444)
    if os.name == "nt":
        import stat as _stat
        os.chmod(locked, _stat.S_IREAD)
    return tree


def test_robust_rmtree_removes_readonly_entries(tmp_path):
    from src.atomic_dir_swap import robust_rmtree
    tree = _make_readonly_tree(tmp_path)
    robust_rmtree(str(tree))
    assert not tree.exists()


def test_robust_rmtree_missing_path_raises_by_default(tmp_path):
    from src.atomic_dir_swap import robust_rmtree
    with pytest.raises(FileNotFoundError):
        robust_rmtree(str(tmp_path / "nope"))


def test_robust_rmtree_missing_path_ignored_when_asked(tmp_path):
    from src.atomic_dir_swap import robust_rmtree
    robust_rmtree(str(tmp_path / "nope"), ignore_errors=True)


def test_robust_rmtree_readonly_dir_entries(tmp_path):
    from src.atomic_dir_swap import robust_rmtree
    tree = tmp_path / "victim2"
    inner = tree / "inner"
    inner.mkdir(parents=True)
    (inner / "f.txt").write_text("x")
    os.chmod(inner, 0o555)
    try:
        robust_rmtree(str(tree))
    finally:
        if inner.exists():
            os.chmod(inner, 0o755)
    assert not tree.exists()
