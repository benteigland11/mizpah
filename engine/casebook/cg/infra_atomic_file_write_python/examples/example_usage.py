"""Example: write text atomically to a tempdir."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.atomic_file_write import atomic_write_bytes, atomic_write_text

with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "hello.txt"
    atomic_write_text(p, "the quick brown fox\n")
    print(f"wrote text:  {p}  →  {p.read_text()!r}")

    q = Path(tmp) / "hello.bin"
    atomic_write_bytes(q, b"\x00\x01\x02", fsync=True)
    print(f"wrote bytes: {q}  →  {q.read_bytes()!r}")
