import io
import os
import tarfile

import pytest

from src.directory_tarball import (
    SourceNotFound,
    TarballError,
    pack_directory,
    write_tarball,
)


def _populate(root: str, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)


def _members(packed_bytes: bytes) -> list[tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(packed_bytes), mode="r:*") as tf:
        return tf.getmembers()


def test_pack_basic_files(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {"a.py": b"x = 1\n", "sub/b.py": b"y = 2\n"})
    packed = pack_directory(str(src))
    names = [m.name for m in _members(packed.data)]
    assert names == ["pkg", "pkg/a.py", "pkg/sub", "pkg/sub/b.py"]


def test_entries_sorted_by_archive_path(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {"z.txt": b"z", "a.txt": b"a", "m/n.txt": b"n"})
    packed = pack_directory(str(src))
    assert list(packed.entries) == sorted(packed.entries)


def test_deterministic_across_runs(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {"a.py": b"x", "sub/b.py": b"y"})
    p1 = pack_directory(str(src))
    p2 = pack_directory(str(src))
    assert p1.data == p2.data
    assert p1.sha256 == p2.sha256


def test_mtime_is_fixed(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {"a.py": b"x"})
    packed = pack_directory(str(src), mtime=1700000000)
    for m in _members(packed.data):
        assert m.mtime == 1700000000


def test_uid_gid_uname_gname_normalized(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {"a.py": b"x"})
    packed = pack_directory(str(src))
    for m in _members(packed.data):
        assert m.uid == 0
        assert m.gid == 0
        assert m.uname == ""
        assert m.gname == ""


def test_file_mode_normalized_to_644(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {"a.py": b"x"})
    os.chmod(os.path.join(str(src), "a.py"), 0o600)
    packed = pack_directory(str(src))
    file_info = next(m for m in _members(packed.data) if m.name.endswith("a.py"))
    assert file_info.mode == 0o644


def test_executable_bit_promotes_mode_to_755(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {"run.sh": b"#!/bin/sh\n"})
    os.chmod(os.path.join(str(src), "run.sh"), 0o755)
    packed = pack_directory(str(src))
    file_info = next(m for m in _members(packed.data) if m.name.endswith("run.sh"))
    assert file_info.mode == 0o755


def test_dir_mode_normalized_to_755(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {"sub/x.py": b"x"})
    packed = pack_directory(str(src))
    dir_info = next(m for m in _members(packed.data) if m.name == "pkg/sub")
    assert dir_info.mode == 0o755


def test_exclude_simple_glob(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {
        "a.py": b"x", "b.pyc": b"x",
        "__pycache__/c.pyc": b"x", "sub/d.py": b"x",
    })
    packed = pack_directory(str(src), exclude=("__pycache__", "*.pyc"))
    names = {m.name for m in _members(packed.data)}
    assert "pkg/__pycache__" not in names
    assert not any(n.endswith(".pyc") for n in names)
    assert "pkg/a.py" in names
    assert "pkg/sub/d.py" in names


def test_exclude_does_not_descend_into_excluded_dir(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {
        "a.py": b"x",
        "node_modules/big_dep/index.js": b"x" * 1000,
    })
    packed = pack_directory(str(src), exclude=("node_modules",))
    names = {m.name for m in _members(packed.data)}
    assert not any("node_modules" in n for n in names)


def test_arcname_overrides_root(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {"a.py": b"x"})
    packed = pack_directory(str(src), arcname="custom-root")
    names = {m.name for m in _members(packed.data)}
    assert "custom-root" in names
    assert "custom-root/a.py" in names


def test_uncompressed_format(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {"a.py": b"x"})
    packed = pack_directory(str(src), compression=None)
    # Uncompressed tar starts with file name padded to 100 bytes.
    with tarfile.open(fileobj=io.BytesIO(packed.data), mode="r:") as tf:
        names = [m.name for m in tf.getmembers()]
    assert names[0] == "pkg"


def test_unsupported_compression_raises(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {"a.py": b"x"})
    with pytest.raises(TarballError):
        pack_directory(str(src), compression="zip")


def test_missing_source_raises(tmp_path):
    with pytest.raises(SourceNotFound):
        pack_directory(str(tmp_path / "nope"))


def test_symlink_stored_as_symlink_by_default(tmp_path):
    src = tmp_path / "pkg"
    os.makedirs(str(src))
    target = os.path.join(str(src), "real.txt")
    with open(target, "wb") as fh:
        fh.write(b"hello")
    link = os.path.join(str(src), "link.txt")
    os.symlink("real.txt", link)
    packed = pack_directory(str(src))
    link_info = next(m for m in _members(packed.data) if m.name.endswith("link.txt"))
    assert link_info.issym()
    assert link_info.linkname == "real.txt"


def test_sha256_changes_when_content_changes(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {"a.py": b"x"})
    p1 = pack_directory(str(src))
    _populate(str(src), {"a.py": b"y"})
    p2 = pack_directory(str(src))
    assert p1.sha256 != p2.sha256


def test_write_tarball_writes_bytes_to_disk(tmp_path):
    src = tmp_path / "pkg"
    _populate(str(src), {"a.py": b"x"})
    packed = pack_directory(str(src))
    out = tmp_path / "out.tar.gz"
    write_tarball(packed, str(out))
    with open(out, "rb") as fh:
        assert fh.read() == packed.data
