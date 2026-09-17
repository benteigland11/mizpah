"""Classify version transitions for republish and bump-policy decisions.

Given a current and proposed semantic version, classify the transition
as one of:

  * same       - identical versions (republish at same version)
  * patch      - same major.minor, patch increased
  * minor      - same major, minor increased
  * major      - major increased
  * downgrade  - proposed is strictly lower than current

Build metadata (`+build`) is ignored when comparing per the semver
spec. Prereleases (`-alpha.1`) are ordered below their normal release,
and lexicographic-with-numeric-aware ordering is applied within
prerelease identifier chains. The classifier exposes the comparison
primitives so callers can layer their own policies (for example,
"reject downgrade", "warn on major", or "block same-version
republish").
"""

import re
from dataclasses import dataclass
from typing import Tuple


_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class VersionError(Exception):
    """Base error for version policy."""


class InvalidVersion(VersionError):
    """Raised when a version string does not match the semver grammar."""


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: Tuple[str, ...] = ()
    build: Tuple[str, ...] = ()

    @property
    def is_prerelease(self) -> bool:
        return len(self.prerelease) > 0


def parse_version(value: str) -> Version:
    """Parse a semver string into a Version. Build metadata is preserved
    but does not participate in ordering, per the semver spec."""
    if not isinstance(value, str):
        raise InvalidVersion(f"version must be a string, got {type(value).__name__}")
    m = _SEMVER_RE.match(value)
    if m is None:
        raise InvalidVersion(f"not a valid semver string: {value!r}")
    pre = tuple(m.group("prerelease").split(".")) if m.group("prerelease") else ()
    build = tuple(m.group("build").split(".")) if m.group("build") else ()
    return Version(
        major=int(m.group("major")),
        minor=int(m.group("minor")),
        patch=int(m.group("patch")),
        prerelease=pre,
        build=build,
    )


def _cmp_prerelease_id(a: str, b: str) -> int:
    """Compare two prerelease identifiers per semver: numeric < alphanumeric,
    numeric ordered numerically, alphanumeric ordered lexically."""
    a_num = a.isdigit()
    b_num = b.isdigit()
    if a_num and b_num:
        ai, bi = int(a), int(b)
        return (ai > bi) - (ai < bi)
    if a_num and not b_num:
        return -1
    if b_num and not a_num:
        return 1
    return (a > b) - (a < b)


def _cmp_prerelease(a: Tuple[str, ...], b: Tuple[str, ...]) -> int:
    """Compare prerelease identifier chains. An empty chain (no prerelease)
    sorts ABOVE any non-empty chain per semver."""
    if a == b:
        return 0
    if not a and b:
        return 1
    if a and not b:
        return -1
    for ai, bi in zip(a, b):
        c = _cmp_prerelease_id(ai, bi)
        if c != 0:
            return c
    return (len(a) > len(b)) - (len(a) < len(b))


def compare_versions(a: Version | str, b: Version | str) -> int:
    """Return -1, 0, or 1 comparing a to b under semver precedence.
    Build metadata is ignored, per spec."""
    if isinstance(a, str):
        a = parse_version(a)
    if isinstance(b, str):
        b = parse_version(b)
    core_a = (a.major, a.minor, a.patch)
    core_b = (b.major, b.minor, b.patch)
    if core_a != core_b:
        return -1 if core_a < core_b else 1
    return _cmp_prerelease(a.prerelease, b.prerelease)


def classify_bump(current: str, proposed: str) -> str:
    """Classify the transition from `current` to `proposed`.

    Returns one of: "same", "patch", "minor", "major", "downgrade".
    """
    cur = parse_version(current)
    nxt = parse_version(proposed)

    cmp_ = compare_versions(cur, nxt)
    if cmp_ == 0:
        return "same"
    if cmp_ > 0:
        return "downgrade"

    if nxt.major != cur.major:
        return "major"
    if nxt.minor != cur.minor:
        return "minor"
    return "patch"


def is_republish(current: str, proposed: str) -> bool:
    """True iff the proposed version equals the current version."""
    return classify_bump(current, proposed) == "same"
