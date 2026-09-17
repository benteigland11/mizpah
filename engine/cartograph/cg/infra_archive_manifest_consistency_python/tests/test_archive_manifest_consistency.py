import io
import tarfile

import pytest

from src.archive_manifest_consistency import (
    ConsistencyError,
    check_consistency,
)


def _build_tar(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_no_declarations_means_ok_with_full_listing():
    data = _build_tar([("a.txt", b"x"), ("b.txt", b"y")])
    r = check_consistency(data)
    assert r.ok is True
    assert r.all_files == ("a.txt", "b.txt")


def test_expected_matches_archive_exactly():
    data = _build_tar([("a.txt", b"x"), ("b.txt", b"y")])
    r = check_consistency(data, expected={"a.txt", "b.txt"})
    assert r.ok is True
    assert r.missing == ()
    assert r.extra == ()


def test_extra_files_in_archive_reported():
    data = _build_tar([("a.txt", b"x"), ("b.txt", b"y"), ("c.txt", b"z")])
    r = check_consistency(data, expected={"a.txt", "b.txt"})
    assert r.ok is False
    assert r.extra == ("c.txt",)


def test_missing_files_reported():
    data = _build_tar([("a.txt", b"x")])
    r = check_consistency(data, expected={"a.txt", "b.txt"})
    assert r.ok is False
    assert r.missing == ("b.txt",)


def test_required_present_passes():
    data = _build_tar([("manifest.json", b"{}"), ("a.txt", b"x")])
    r = check_consistency(data, required={"manifest.json"})
    assert r.ok is True
    assert r.required_missing == ()


def test_required_missing_reported():
    data = _build_tar([("a.txt", b"x")])
    r = check_consistency(data, required={"manifest.json"})
    assert r.ok is False
    assert r.required_missing == ("manifest.json",)


def test_forbidden_absent_passes():
    data = _build_tar([("src/a.py", b"x")])
    r = check_consistency(data, forbidden={"__pycache__/junk.pyc"})
    assert r.ok is True
    assert r.forbidden_present == ()


def test_forbidden_present_reported():
    data = _build_tar([("src/a.py", b"x"), ("__pycache__/junk.pyc", b"y")])
    r = check_consistency(data, forbidden={"__pycache__/junk.pyc"})
    assert r.ok is False
    assert r.forbidden_present == ("__pycache__/junk.pyc",)


def test_combined_declarations_all_evaluated():
    data = _build_tar([
        ("manifest.json", b"{}"),
        ("a.txt", b"x"),
        ("__pycache__/junk.pyc", b"y"),
    ])
    r = check_consistency(
        data,
        expected={"manifest.json", "a.txt"},
        required={"manifest.json"},
        forbidden={"__pycache__/junk.pyc"},
    )
    assert r.ok is False
    assert r.extra == ("__pycache__/junk.pyc",)
    assert r.forbidden_present == ("__pycache__/junk.pyc",)
    assert r.required_missing == ()


def test_directories_excluded_from_file_list():
    """Directory entries should not appear in all_files."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        dir_info = tarfile.TarInfo(name="pkg")
        dir_info.type = tarfile.DIRTYPE
        tf.addfile(dir_info)
        file_info = tarfile.TarInfo(name="pkg/a.py")
        file_info.size = 1
        tf.addfile(file_info, io.BytesIO(b"x"))
    r = check_consistency(buf.getvalue())
    assert r.all_files == ("pkg/a.py",)


def test_extracts_from_file_path(tmp_path):
    data = _build_tar([("a.txt", b"hello")])
    p = tmp_path / "in.tar"
    p.write_bytes(data)
    r = check_consistency(str(p), expected={"a.txt"})
    assert r.ok is True


def test_missing_archive_path_raises(tmp_path):
    with pytest.raises(ConsistencyError):
        check_consistency(str(tmp_path / "nope.tar"))


def test_archive_must_be_bytes_or_str():
    with pytest.raises(ConsistencyError):
        check_consistency(123)  # type: ignore[arg-type]


def test_all_files_sorted():
    data = _build_tar([("z.txt", b"z"), ("a.txt", b"a"), ("m.txt", b"m")])
    r = check_consistency(data)
    assert r.all_files == ("a.txt", "m.txt", "z.txt")


def test_empty_expected_set_means_archive_must_be_empty():
    """Passing an empty set as `expected` (vs None) is a real declaration."""
    data = _build_tar([("a.txt", b"x")])
    r = check_consistency(data, expected=set())
    assert r.ok is False
    assert r.extra == ("a.txt",)


def test_empty_archive():
    data = _build_tar([])
    r = check_consistency(data, expected=set())
    assert r.ok is True
    assert r.all_files == ()
