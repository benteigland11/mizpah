"""Example: load a Python config file and extract a typed instance.

Demonstrates the two modes of load_instance: explicit var_name and
auto-discovery by type. Writes a tiny fixture file to the current
working directory, loads it, and cleans up.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.python_module_loader import load_instance


def main() -> None:
    fixture_body = (
        "# A user-authored config-as-code file.\n"
        "settings = {'theme': 'dark', 'rows': 24, 'cols': 80}\n"
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as f:
        f.write(fixture_body)
        path = f.name

    try:
        by_name = load_instance(path, dict, var_name="settings")
        print(f"Named lookup -> {by_name}")

        auto = load_instance(path, dict)
        print(f"Auto-discover -> {auto}")
    finally:
        os.unlink(path)


if __name__ == "__main__":
    main()
