"""Example: verify a tarball matches a declared manifest.

Builds an in-memory archive that includes an unintended __pycache__ file
and shows the consistency check catching both an extra entry and a
forbidden entry against caller-supplied declarations.
"""

import io
import os
import sys
import tarfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.archive_manifest_consistency import check_consistency


def _build(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def main() -> None:
    archive = _build([
        ("manifest.json", b'{"id": "example"}'),
        ("src/a.py", b"x = 1\n"),
        ("src/b.py", b"y = 2\n"),
        ("__pycache__/a.cpython-312.pyc", b"compiled"),
    ])

    report = check_consistency(
        archive,
        expected={"manifest.json", "src/a.py", "src/b.py"},
        required={"manifest.json"},
        forbidden={"__pycache__/a.cpython-312.pyc"},
    )

    print(f"ok:                {report.ok}")
    print(f"all_files:         {list(report.all_files)}")
    print(f"missing:           {list(report.missing)}")
    print(f"extra:             {list(report.extra)}")
    print(f"required_missing:  {list(report.required_missing)}")
    print(f"forbidden_present: {list(report.forbidden_present)}")


if __name__ == "__main__":
    main()
