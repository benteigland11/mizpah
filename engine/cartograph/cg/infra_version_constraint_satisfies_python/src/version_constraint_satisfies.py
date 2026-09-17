"""Check whether an installed version satisfies a declared dependency constraint.

Cross-ecosystem version-constraint satisfaction with a single tolerant
comparator. Understands the operators that appear in PyPI, npm, and Composer
dependency declarations:

    >=  >  <=  <  ==  !=        comparison / pinning
    ~=                          PEP 440 compatible release
    ^                           npm/Composer caret (same major)
    ~                           npm/Composer tilde (same major.minor)
    <bare version>              treated as a floor (>=)

and a parser that splits a raw dependency string into ``(name, spec)``,
tolerating extras (``pkg[extra]``), environment markers (``pkg; marker``),
and scoped npm names (``@scope/pkg``).

Version parsing is deliberately lenient: a ``v`` prefix is stripped, build
metadata (``+sha``) is ignored, partial versions (``1.5``) are padded to
three components, and prereleases (``1.0.0-rc.1``) order below their release.
Anything the comparator cannot parse yields ``None`` from :func:`satisfies`
so callers can fail safe rather than trust an undecidable comparison.

Stdlib only; fully self-contained.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# Operators recognised at the start of a spec, longest first so the two-char
# forms win over their one-char prefixes.
_OPS: Tuple[str, ...] = (">=", "<=", "==", "!=", "~=", ">", "<", "^", "~")
_VER_PREFIX_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*")


def parse_requirement(dep: str) -> Tuple[str, str]:
    """Split a raw dependency declaration into ``(name, spec)``.

    Handles extras (``pkg[extra]>=1.0``), environment markers
    (``pkg>=1.0; python_version>'3'``), and scoped npm names
    (``@scope/pkg@>=1.0`` or ``@scope/pkg>=1.0``). Returns ``("", "")`` for
    input that has no recognisable package name.
    """
    if not isinstance(dep, str):
        return "", ""
    s = dep.split(";", 1)[0].strip()          # drop environment markers
    if not s:
        return "", ""
    # A leading '@' is a scoped npm name; skip it so its separators are not
    # mistaken for a version operator.
    search_from = 1 if s.startswith("@") else 0
    op_pos: Optional[int] = None
    m = re.search(r"[<>=!~^]", s[search_from:])
    if m is not None:
        op_pos = search_from + m.start()
    if op_pos is None:
        name, spec = s, ""
    else:
        name, spec = s[:op_pos], s[op_pos:]
    # An npm scoped pin can read "@scope/pkg@>=1.0" - drop a trailing '@'.
    name = name.split("[", 1)[0].rstrip("@").strip()
    return name, spec.strip()


def _parse(value: str) -> Optional[Tuple[int, int, int, Tuple[str, ...]]]:
    """Parse a version into ``(major, minor, patch, prerelease)`` or ``None``."""
    if not isinstance(value, str):
        return None
    s = value.strip().lstrip("vV")
    if not s:
        return None
    s = s.split("+", 1)[0]                     # drop build metadata
    if "-" in s:
        core_str, pre_str = s.split("-", 1)
        prerelease = tuple(pre_str.split("."))
    else:
        core_str, prerelease = s, ()
    m = _VER_PREFIX_RE.match(core_str)
    if m is None:
        return None
    nums = [int(x) for x in m.group(0).split(".")[:3]]
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2], prerelease


def _cmp_prerelease_id(a: str, b: str) -> int:
    """Compare two prerelease identifiers: numeric < alphanumeric."""
    a_num, b_num = a.isdigit(), b.isdigit()
    if a_num and b_num:
        ai, bi = int(a), int(b)
        return (ai > bi) - (ai < bi)
    if a_num and not b_num:
        return -1
    if b_num and not a_num:
        return 1
    return (a > b) - (a < b)


def _cmp_prerelease(a: Tuple[str, ...], b: Tuple[str, ...]) -> int:
    """Compare prerelease chains; an empty chain (a release) sorts ABOVE one."""
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


def compare(a: str, b: str) -> Optional[int]:
    """Return -1, 0, or 1 comparing version ``a`` to ``b``, or ``None``.

    ``None`` means either side could not be parsed. Build metadata is ignored
    and prereleases order below their corresponding release, per semver.
    """
    pa, pb = _parse(a), _parse(b)
    if pa is None or pb is None:
        return None
    core_a, core_b = pa[:3], pb[:3]
    if core_a != core_b:
        return -1 if core_a < core_b else 1
    return _cmp_prerelease(pa[3], pb[3])


def satisfies(installed: str, spec: str) -> Optional[bool]:
    """Return whether ``installed`` satisfies ``spec``.

    ``True``/``False`` for a decidable comparison; ``None`` when a version
    cannot be parsed (callers should fail safe). An empty spec is always
    satisfied. A bare version (no operator) is treated as a floor (``>=``).
    """
    if not isinstance(spec, str) or not spec.strip():
        return True
    spec = spec.strip()
    op = next((o for o in _OPS if spec.startswith(o)), "")
    want = spec[len(op):].strip()
    op = op or ">="                           # bare version -> floor
    if not want:
        return True
    c = compare(installed, want)
    if c is None:
        return None
    if op == "==":
        return c == 0
    if op == "!=":
        return c != 0
    if op == ">=":
        return c >= 0
    if op == ">":
        return c > 0
    if op == "<=":
        return c <= 0
    if op == "<":
        return c < 0

    pi, pw = _parse(installed), _parse(want)
    if pi is None or pw is None:
        return None
    if op == "^":                             # npm caret: same major
        return c >= 0 and pi[0] == pw[0]
    if op == "~":                             # npm tilde: same major.minor
        return c >= 0 and pi[0] == pw[0] and pi[1] == pw[1]
    if op == "~=":                            # PEP 440 compatible release
        if c < 0:
            return False
        match = _VER_PREFIX_RE.match(want)
        components = len(match.group(0).split(".")) if match else 0
        if components >= 3:
            return pi[0] == pw[0] and pi[1] == pw[1]
        return pi[0] == pw[0]
    return None
