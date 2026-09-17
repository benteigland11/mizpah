"""Example: classify version transitions and gate a republish.

Demonstrates the typical usage in a publish flow: classify the bump,
then layer policy ("reject downgrade", "block same-version republish
without --force") on top of the classification.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.version_bump_policy import classify_bump, is_republish


def main() -> None:
    transitions = [
        ("1.0.0", "1.0.0"),
        ("1.0.0", "1.0.1"),
        ("1.0.0", "1.1.0"),
        ("1.0.0", "2.0.0"),
        ("2.0.0", "1.9.9"),
        ("1.0.0-alpha.1", "1.0.0-alpha.2"),
        ("1.0.0-alpha", "1.0.0"),
    ]
    for cur, nxt in transitions:
        kind = classify_bump(cur, nxt)
        print(f"{cur:<18} -> {nxt:<18} {kind}")

    force = False
    cur, nxt = "1.0.0", "1.0.0"
    if is_republish(cur, nxt) and not force:
        print(f"\nblocked republish of {cur} (pass --force to override)")


if __name__ == "__main__":
    main()
