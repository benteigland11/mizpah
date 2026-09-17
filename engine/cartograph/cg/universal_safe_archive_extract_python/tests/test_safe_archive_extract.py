import io
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.safe_archive_extract import (
    safe_extractall,
    safe_extract_zip,
    assert_members_safe,
    UnsafeArchiveError,
)


def _zip_bytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    buf.seek(0)
    return buf


def test_allows_normal_members(tmp_path):
    dest = tmp_path / "out"
    with zipfile.ZipFile(_zip_bytes({"src/a.py": "x", "widget.json": "{}"})) as zf:
        safe_extractall(zf, str(dest))
    assert (dest / "src" / "a.py").read_text() == "x"
    assert (dest / "widget.json").exists()


@pytest.mark.parametrize("evil", [
    "../escape.py",
    "../../etc/passwd",
    "src/../../escape.py",
    "/abs/escape.py",
])
def test_rejects_traversal(tmp_path, evil):
    dest = tmp_path / "out"
    with zipfile.ZipFile(_zip_bytes({evil: "pwned"})) as zf:
        with pytest.raises(UnsafeArchiveError):
            safe_extractall(zf, str(dest))
    assert not (tmp_path / "escape.py").exists()
    assert not (tmp_path.parent / "escape.py").exists()


def test_writes_nothing_when_one_member_is_unsafe(tmp_path):
    dest = tmp_path / "out"
    with zipfile.ZipFile(_zip_bytes({"ok.py": "x", "../evil.py": "x"})) as zf:
        with pytest.raises(UnsafeArchiveError):
            safe_extractall(zf, str(dest))
    # Guard runs before any extraction, so the safe member isn't written either.
    assert not (dest / "ok.py").exists()


def test_creates_destination_if_missing(tmp_path):
    dest = tmp_path / "nested" / "out"
    with zipfile.ZipFile(_zip_bytes({"f.txt": "v"})) as zf:
        safe_extractall(zf, str(dest))
    assert (dest / "f.txt").read_text() == "v"


def test_safe_extract_zip_from_path(tmp_path):
    zpath = tmp_path / "a.zip"
    zpath.write_bytes(_zip_bytes({"f.txt": "hello"}).getvalue())
    dest = tmp_path / "out"
    safe_extract_zip(str(zpath), str(dest))
    assert (dest / "f.txt").read_text() == "hello"


def test_safe_extract_zip_rejects_traversal_from_path(tmp_path):
    zpath = tmp_path / "evil.zip"
    zpath.write_bytes(_zip_bytes({"../evil.py": "x"}).getvalue())
    with pytest.raises(UnsafeArchiveError):
        safe_extract_zip(str(zpath), str(tmp_path / "out"))


def test_assert_members_safe_passes_clean_names(tmp_path):
    assert_members_safe(["src/a.py", "widget.json", "tests/t.py"], str(tmp_path))


@pytest.mark.parametrize("evil", ["../x", "/abs/x", "a/../../x"])
def test_assert_members_safe_rejects_escape(tmp_path, evil):
    with pytest.raises(UnsafeArchiveError):
        assert_members_safe(["ok.py", evil], str(tmp_path))


def test_unsafe_archive_error_is_valueerror():
    assert issubclass(UnsafeArchiveError, ValueError)
