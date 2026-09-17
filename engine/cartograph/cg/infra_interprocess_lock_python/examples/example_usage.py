"""Example: serialize a critical section with a cross-process file lock."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.interprocess_lock import file_lock, LockBusy

with tempfile.TemporaryDirectory() as d:
    lock_path = os.path.join(d, "library.lock")

    # Blocking acquire: wrap any multi-step mutation so a second process
    # can't interleave. Waits (via the OS) until the lock is free.
    with file_lock(lock_path):
        print("holding the lock; doing a multi-step update safely")
        # ... rename-swap a directory, write a manifest, archive a version ...

    print("released")

    # Reentrant on one thread: a nested acquire reuses the held lock.
    with file_lock(lock_path):
        with file_lock(lock_path):
            print("nested acquire on the same thread does not deadlock")

    # Non-blocking acquire: surface contention as a clean exception instead
    # of waiting. A caller can build a timed retry loop around this.
    try:
        with file_lock(lock_path, blocking=False):
            print("acquired without waiting (uncontended)")
    except LockBusy as e:
        print(f"would report contention: {e}")
