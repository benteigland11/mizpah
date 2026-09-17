"""
Example usage of File Stamp.

Demonstrates fingerprinting a directory and using stamps to detect changes.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.file_stamp import (
    collect_files,
    fingerprint,
    write_stamp,
    read_stamp,
    is_stamp_valid,
)

# Create a temporary project to stamp
with tempfile.TemporaryDirectory() as project:
    src = os.path.join(project, "src")
    os.makedirs(src)

    with open(os.path.join(src, "main.py"), "w") as f:
        f.write("def run(): pass\n")
    with open(os.path.join(src, "util.py"), "w") as f:
        f.write("helpers = True\n")

    # 1. Collect files
    files = collect_files(project)
    sys.stdout.write(f"Found {len(files)} files\n")

    # 2. Compute fingerprint
    fp = fingerprint(project)
    sys.stdout.write(f"Fingerprint: {fp[:16]}...\n")

    # 3. Write a stamp with metadata
    stamp = write_stamp(project, metadata={"language": "python", "tool": "example"})
    sys.stdout.write(f"Stamp written at {stamp['stamped_at']}\n")

    # 4. Check validity - should be valid (nothing changed)
    valid = is_stamp_valid(project, metadata_match={"language": "python"})
    sys.stdout.write(f"Valid (no changes): {valid}\n")

    # 5. Modify a file
    with open(os.path.join(src, "main.py"), "w") as f:
        f.write("def run(): return 42\n")

    # 6. Check again - should be invalid (file changed)
    valid = is_stamp_valid(project, metadata_match={"language": "python"})
    sys.stdout.write(f"Valid (after edit): {valid}\n")

    # 7. Re-stamp
    write_stamp(project, metadata={"language": "python", "tool": "example"})
    valid = is_stamp_valid(project, metadata_match={"language": "python"})
    sys.stdout.write(f"Valid (after re-stamp): {valid}\n")
