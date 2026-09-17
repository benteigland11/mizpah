"""Demonstrate the cache-around-a-callable pattern.

A short-lived CLI wants to avoid paying for an expensive lookup on every
invocation. Wrap the call in a get-then-set helper backed by ttl-disk-cache:
first call computes and stores; subsequent calls within the TTL window
short-circuit on the disk read.

Runs offline with a faked-out 'expensive lookup'. Tear-down at the end
keeps the example directory clean for re-runs.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.ttl_disk_cache import get, set, clear


def expensive_lookup(user_id: str) -> dict:
    """Stand-in for a slow network call. In real use this would be an
    HTTP request, a database read, or anything else worth caching."""
    return {"user_id": user_id, "tier": "gold", "lookups": 1}


def cached_lookup(cache_root: str, user_id: str, ttl_seconds: float) -> dict:
    hit = get(cache_root, user_id)
    if hit is not None:
        return hit
    value = expensive_lookup(user_id)
    set(cache_root, user_id, value, ttl_seconds)
    return value


def main() -> None:
    with tempfile.TemporaryDirectory() as cache_root:
        first = cached_lookup(cache_root, "ada", ttl_seconds=60)
        print(f"first call (miss, computed): {first}")

        second = cached_lookup(cache_root, "ada", ttl_seconds=60)
        print(f"second call (hit, from disk): {second}")
        assert first == second, "cache should return the stored value"

        # Inspect what's on disk.
        files = sorted(os.listdir(cache_root))
        print(f"cache files on disk: {files}")

        # Targeted invalidation — useful when underlying data changes.
        clear(cache_root, "ada")
        assert get(cache_root, "ada") is None
        print("after clear: cache miss as expected")


if __name__ == "__main__":
    main()
