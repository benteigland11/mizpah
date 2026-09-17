"""
Example usage of Version Constraint Satisfies.

Demonstrates parsing a raw dependency declaration and checking whether an
installed version satisfies the declared constraint - the core of a
dependency-cache "is this env still valid?" gate.

Runs and exits cleanly with no user input, network, or external services.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.version_constraint_satisfies import (
    compare,
    parse_requirement,
    satisfies,
)

# A widget declared this dependency; the resolver installed concrete versions.
declared = "requests>=2.0.0"
installed_versions = {"requests": "2.31.0"}

name, spec = parse_requirement(declared)
print(f"Parsed {declared!r} -> name={name!r}, spec={spec!r}")

installed = installed_versions.get(name)
ok = satisfies(installed, spec)
print(f"Installed {installed} satisfies {spec}? {ok}")
assert ok is True

# A floor that the installed version fails.
print(f"2.31.0 satisfies >=3.0.0? {satisfies('2.31.0', '>=3.0.0')}")
assert satisfies("2.31.0", ">=3.0.0") is False

# npm-style caret range across an ecosystem boundary.
print(f"4.18.0 satisfies ^4.17.0? {satisfies('4.18.0', '^4.17.0')}")
assert satisfies("4.18.0", "^4.17.0") is True

# Direct version comparison: -1 / 0 / 1.
print(f"compare('2.0.0', '1.9.0') = {compare('2.0.0', '1.9.0')}")
assert compare("2.0.0", "1.9.0") == 1

# Unparseable versions fail safe as None so callers can rebuild rather than
# trust an undecidable comparison.
print(f"satisfies('garbage', '>=1.0.0') = {satisfies('garbage', '>=1.0.0')}")
assert satisfies("garbage", ">=1.0.0") is None

print("OK")
