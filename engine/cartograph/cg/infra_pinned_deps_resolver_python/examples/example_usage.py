"""Example: resolve a list of pinned deps against an in-memory registry.

The lookup callable is the only abstraction the resolver knows about, so
the example uses a hardcoded version map. Real consumers would close over
a registry client, a filesystem walker, or a multi-tier composed lookup.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.pinned_deps_resolver import partition, resolve_pins


def main() -> None:
    registry = {
        "example-retry-python": "1.2.0",
        "example-validator-python": "1.0.0",
    }

    def lookup(pin_id: str) -> str | None:
        return registry.get(pin_id)

    pins = [
        {"id": "example-retry-python", "version": "1.2.0"},
        {"id": "example-validator-python", "version": "0.9.0"},
        {"id": "example-router-python", "version": "1.0.0"},
    ]
    resolved = resolve_pins(pins, lookup)
    grouped = partition(resolved)

    print("ok:")
    for r in grouped["ok"]:
        print(f"  {r.id} @ {r.pinned}")
    print("version-mismatch:")
    for r in grouped["version-mismatch"]:
        print(f"  {r.id}: pinned {r.pinned}, registry has {r.found}")
    print("missing:")
    for r in grouped["missing"]:
        print(f"  {r.id} @ {r.pinned}")


if __name__ == "__main__":
    main()
