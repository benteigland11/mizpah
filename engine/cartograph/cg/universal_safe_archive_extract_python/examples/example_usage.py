"""Example: extract a zip while refusing any member that escapes the target."""
import io
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.safe_archive_extract import safe_extractall, UnsafeArchiveError


def _make_zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    buf.seek(0)
    return buf


with tempfile.TemporaryDirectory() as d:
    dest = os.path.join(d, "out")

    # A well-formed archive extracts normally.
    with zipfile.ZipFile(_make_zip({"src/main.py": "print(1)", "widget.json": "{}"})) as zf:
        safe_extractall(zf, dest)
    print("extracted:", sorted(os.listdir(dest)))

    # A malicious member is refused before anything is written.
    with zipfile.ZipFile(_make_zip({"../../escape.py": "pwned"})) as zf:
        try:
            safe_extractall(zf, dest)
        except UnsafeArchiveError as e:
            print("blocked zip-slip:", e)
