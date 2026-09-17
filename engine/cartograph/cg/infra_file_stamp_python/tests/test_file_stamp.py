import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.file_stamp import (
    collect_files,
    fingerprint,
    write_stamp,
    read_stamp,
    is_stamp_valid,
    adopt_stamp,
    STAMP_FILE,
)


def _make_tree(tmp_path):
    """Create a small file tree for testing."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hello')")
    (src / "util.py").write_text("x = 1")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_main.py").write_text("assert True")
    (tmp_path / "widget.json").write_text('{"name": "test"}')
    return tmp_path


def test_collect_files_defaults(tmp_path):
    root = _make_tree(tmp_path)
    files = collect_files(str(root))
    names = [os.path.basename(f) for f in files]
    assert "main.py" in names
    assert "util.py" in names
    assert "test_main.py" in names
    assert "widget.json" in names


def test_collect_files_skips_pycache(tmp_path):
    root = _make_tree(tmp_path)
    cache = tmp_path / "src" / "__pycache__"
    cache.mkdir()
    (cache / "main.cpython-312.pyc").write_bytes(b"\x00")
    files = collect_files(str(root))
    for f in files:
        assert "__pycache__" not in f


def test_collect_files_skips_compiled_binaries(tmp_path):
    """Extensionless executables (nimble test artifacts, native builds) must
    not feed the fingerprint — they're transient and get cleaned up between
    validate and re-read, which would invalidate the stamp instantly."""
    root = _make_tree(tmp_path)
    # Simulate a compiled test binary Nim leaves in tests/
    elf = tmp_path / "tests" / "test_main"
    elf.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 32)
    # And a Windows PE artifact
    pe = tmp_path / "tests" / "test_win"
    pe.write_bytes(b"MZ\x90\x00" + b"\x00" * 32)
    # And a WASM artifact
    wasm = tmp_path / "tests" / "test_wasm"
    wasm.write_bytes(b"\x00asm\x01\x00\x00\x00")
    files = collect_files(str(root))
    assert str(elf) not in files
    assert str(pe) not in files
    assert str(wasm) not in files
    # Sanity: real source is still there
    assert any(f.endswith("test_main.py") for f in files)


def test_fingerprint_stable_after_binary_added(tmp_path):
    """Dropping a compiled binary into tests/ must NOT change the fingerprint —
    otherwise the stamp would go stale the instant cleanup runs."""
    root = _make_tree(tmp_path)
    fp_before = fingerprint(str(root))
    (tmp_path / "tests" / "test_main").write_bytes(b"\x7fELF" + b"\x00" * 100)
    fp_after = fingerprint(str(root))
    assert fp_before == fp_after


def test_collect_files_custom_patterns(tmp_path):
    root = _make_tree(tmp_path)
    patterns = [os.path.join(str(root), "src", "*.py")]
    files = collect_files(str(root), patterns=patterns)
    names = [os.path.basename(f) for f in files]
    assert "main.py" in names
    assert "test_main.py" not in names


def test_fingerprint_deterministic(tmp_path):
    root = _make_tree(tmp_path)
    fp1 = fingerprint(str(root))
    fp2 = fingerprint(str(root))
    assert fp1 == fp2
    assert len(fp1) == 64  # SHA-256 hex


def test_fingerprint_changes_on_edit(tmp_path):
    root = _make_tree(tmp_path)
    fp1 = fingerprint(str(root))
    (tmp_path / "src" / "main.py").write_text("print('changed')")
    fp2 = fingerprint(str(root))
    assert fp1 != fp2


def test_fingerprint_changes_on_new_file(tmp_path):
    root = _make_tree(tmp_path)
    fp1 = fingerprint(str(root))
    (tmp_path / "src" / "new.py").write_text("y = 2")
    fp2 = fingerprint(str(root))
    assert fp1 != fp2


def test_fingerprint_explicit_files(tmp_path):
    root = _make_tree(tmp_path)
    only = [str(tmp_path / "src" / "main.py")]
    fp = fingerprint(str(root), files=only)
    assert len(fp) == 64


def test_write_and_read_stamp(tmp_path):
    root = _make_tree(tmp_path)
    stamp = write_stamp(str(root))
    assert "fingerprint" in stamp
    assert "stamped_at" in stamp

    loaded = read_stamp(str(root))
    assert loaded is not None
    assert loaded["fingerprint"] == stamp["fingerprint"]


def test_write_stamp_with_metadata(tmp_path):
    root = _make_tree(tmp_path)
    stamp = write_stamp(str(root), metadata={"language": "python", "version": "1.0"})
    assert stamp["language"] == "python"
    assert stamp["version"] == "1.0"

    loaded = read_stamp(str(root))
    assert loaded["language"] == "python"


def test_read_stamp_missing(tmp_path):
    assert read_stamp(str(tmp_path)) is None


def test_is_stamp_valid_fresh(tmp_path):
    root = _make_tree(tmp_path)
    write_stamp(str(root))
    assert is_stamp_valid(str(root)) is True


def test_is_stamp_valid_after_change(tmp_path):
    root = _make_tree(tmp_path)
    write_stamp(str(root))
    (tmp_path / "src" / "main.py").write_text("print('changed')")
    assert is_stamp_valid(str(root)) is False


def test_is_stamp_valid_metadata_match(tmp_path):
    root = _make_tree(tmp_path)
    write_stamp(str(root), metadata={"language": "python"})
    assert is_stamp_valid(str(root), metadata_match={"language": "python"}) is True
    assert is_stamp_valid(str(root), metadata_match={"language": "nim"}) is False


def test_is_stamp_valid_no_stamp(tmp_path):
    root = _make_tree(tmp_path)
    assert is_stamp_valid(str(root)) is False


def test_custom_stamp_name(tmp_path):
    root = _make_tree(tmp_path)
    write_stamp(str(root), stamp_name=".my_stamp.json")
    assert os.path.exists(str(tmp_path / ".my_stamp.json"))
    assert is_stamp_valid(str(root), stamp_name=".my_stamp.json") is True
    assert is_stamp_valid(str(root)) is False  # default name doesn't exist


def test_adopt_stamp_matching(tmp_path):
    root = _make_tree(tmp_path)
    stamp = write_stamp(str(root), metadata={"language": "python"})
    os.remove(str(tmp_path / STAMP_FILE))
    assert adopt_stamp(str(root), stamp) is True
    adopted = read_stamp(str(root))
    assert adopted == stamp  # verbatim, original stamped_at preserved
    assert is_stamp_valid(str(root)) is True


def test_adopt_stamp_mismatch_writes_nothing(tmp_path):
    root = _make_tree(tmp_path)
    stamp = write_stamp(str(root))
    os.remove(str(tmp_path / STAMP_FILE))
    (tmp_path / "src" / "main.py").write_text("print('tampered')")
    assert adopt_stamp(str(root), stamp) is False
    assert read_stamp(str(root)) is None


def test_adopt_stamp_missing_fingerprint(tmp_path):
    root = _make_tree(tmp_path)
    assert adopt_stamp(str(root), {"language": "python"}) is False
    assert adopt_stamp(str(root), None) is False
    assert read_stamp(str(root)) is None


def test_adopt_stamp_custom_name_and_kwargs(tmp_path):
    root = _make_tree(tmp_path)
    stamp = write_stamp(str(root), stamp_name=".other.json",
                        patterns=[os.path.join(str(root), "src", "**", "*")])
    os.remove(str(tmp_path / ".other.json"))
    ok = adopt_stamp(str(root), stamp, stamp_name=".other.json",
                     patterns=[os.path.join(str(root), "src", "**", "*")])
    assert ok is True
    assert os.path.exists(str(tmp_path / ".other.json"))
