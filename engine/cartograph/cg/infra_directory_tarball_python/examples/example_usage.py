"""Example: pack a small in-memory directory into a deterministic tarball.

Builds a temp directory, packs it twice, and verifies the byte output
is identical across runs (the value proposition of the widget).
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.directory_tarball import pack_directory


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "example_pkg")
        os.makedirs(os.path.join(src, "sub"))
        with open(os.path.join(src, "a.py"), "wb") as fh:
            fh.write(b"x = 1\n")
        with open(os.path.join(src, "sub", "b.py"), "wb") as fh:
            fh.write(b"y = 2\n")
        with open(os.path.join(src, "ignore.pyc"), "wb") as fh:
            fh.write(b"compiled\n")

        first = pack_directory(src, exclude=("*.pyc",))
        second = pack_directory(src, exclude=("*.pyc",))

        print("entries:")
        for e in first.entries:
            print(f"  {e}")
        print(f"sha256:        {first.sha256}")
        print(f"deterministic: {first.data == second.data}")


if __name__ == "__main__":
    main()
