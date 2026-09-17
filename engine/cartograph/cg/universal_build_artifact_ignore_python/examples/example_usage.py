"""Example: filter build artifacts when walking a project tree."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.build_artifact_ignore import (
    excludes_for,
    filter_dirs,
    should_skip,
    supported_languages,
)


def main() -> None:
    print("Known language tags:", ", ".join(supported_languages()))

    py_excludes = excludes_for(language="python")
    print(f"\nPython excludes ({len(py_excludes)}):", sorted(py_excludes))

    polyglot = excludes_for(languages=["python", "angular", "rust"])
    print(f"\nPolyglot excludes ({len(polyglot)}):", sorted(polyglot))

    with tempfile.TemporaryDirectory() as tmp:
        for sub in ("src/lib", "node_modules/foo", ".angular/cache",
                    "tests", "__pycache__"):
            os.makedirs(os.path.join(tmp, sub))
        open(os.path.join(tmp, "src/lib/app.py"), "w").close()
        open(os.path.join(tmp, "node_modules/foo/big.bin"), "w").close()
        open(os.path.join(tmp, ".angular/cache/x.json"), "w").close()
        open(os.path.join(tmp, "tests/test_app.py"), "w").close()

        excludes = excludes_for(languages=["python", "angular"])
        kept: list[str] = []
        for root, dirs, files in os.walk(tmp):
            dirs[:] = filter_dirs(dirs, excludes)
            for fname in files:
                kept.append(os.path.relpath(os.path.join(root, fname), tmp))

        print("\nFiles kept after filter:")
        for path in sorted(kept):
            print(f"  {path}")

        sample = "node_modules/foo/big.bin"
        print(f"\nshould_skip({sample!r}) -> {should_skip(sample, excludes)}")


if __name__ == "__main__":
    main()
