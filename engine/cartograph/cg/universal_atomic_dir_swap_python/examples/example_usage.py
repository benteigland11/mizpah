"""Example: replace a directory atomically, never tearing down the live copy."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.atomic_dir_swap import staged_dir, atomic_swap_dir

with tempfile.TemporaryDirectory() as root:
    dest = os.path.join(root, "widget")
    os.makedirs(dest)
    with open(os.path.join(dest, "v1.txt"), "w") as f:
        f.write("version 1")

    # Build the whole replacement in a sibling temp dir; it swaps in only on
    # clean exit. If the block raised, `dest` would keep "version 1".
    with staged_dir(dest) as build:
        with open(os.path.join(build, "v2.txt"), "w") as f:
            f.write("version 2")
    print("after swap:", sorted(os.listdir(dest)))  # ['v2.txt'] - fully replaced

    # Lower-level: swap a directory you built yourself.
    other = os.path.join(root, ".cg-new-demo")
    os.makedirs(other)
    with open(os.path.join(other, "v3.txt"), "w") as f:
        f.write("version 3")
    atomic_swap_dir(other, dest)
    print("after manual swap:", sorted(os.listdir(dest)))  # ['v3.txt']
