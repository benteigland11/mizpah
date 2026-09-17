"""Tests for the version-constraint satisfaction widget."""

import pytest

from src.version_constraint_satisfies import (
    compare,
    parse_requirement,
    satisfies,
)


@pytest.mark.parametrize(
    "dep,expected",
    [
        ("requests>=2.0.0", ("requests", ">=2.0.0")),
        ("requests", ("requests", "")),
        ("fastapi[all]>=0.128.0", ("fastapi", ">=0.128.0")),
        ("pkg>=1.0; python_version>'3'", ("pkg", ">=1.0")),
        ("@angular/core>=17.0.0", ("@angular/core", ">=17.0.0")),
        ("@scope/pkg@>=1.0.0", ("@scope/pkg", ">=1.0.0")),
        ("lodash^4.17.0", ("lodash", "^4.17.0")),
        ("psr/log~1.2.0", ("psr/log", "~1.2.0")),
        ("monolog/monolog", ("monolog/monolog", "")),
        ("", ("", "")),
    ],
)
def test_parse_requirement(dep, expected):
    assert parse_requirement(dep) == expected


def test_parse_requirement_non_string():
    assert parse_requirement(None) == ("", "")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("2.0.0", "1.0.0", 1),
        ("1.0.0", "2.0.0", -1),
        ("1.2.3", "1.2.3", 0),
        ("1.5", "1.5.0", 0),          # partial padded
        ("v2.1.0", "2.1.0", 0),       # leading v stripped
        ("1.0.0+build", "1.0.0", 0),  # build metadata ignored
        ("1.0.0-rc.1", "1.0.0", -1),  # prerelease below release
        ("1.0.0-rc.2", "1.0.0-rc.1", 1),
        ("1.0.0-alpha", "1.0.0-beta", -1),
    ],
)
def test_compare(a, b, expected):
    assert compare(a, b) == expected


def test_compare_unparseable_returns_none():
    assert compare("not-a-version", "1.0.0") is None
    assert compare("1.0.0", "") is None


@pytest.mark.parametrize(
    "installed,spec,expected",
    [
        ("2.31.0", ">=2.0.0", True),
        ("1.9.0", ">=2.0.0", False),
        ("2.0.0", "==2.0.0", True),
        ("2.0.1", "==2.0.0", False),
        ("2.0.1", "!=2.0.0", True),
        ("2.0.0", "!=2.0.0", False),
        ("1.0.0", ">1.0.0", False),
        ("1.0.1", ">1.0.0", True),
        ("1.0.0", "<=1.0.0", True),
        ("0.9.0", "<1.0.0", True),
        ("1.5", ">=1.0", True),       # partial versions
        ("2.5.0", "", True),          # empty spec
        ("2.5.0", "2.0.0", True),     # bare version -> floor
        ("1.9.0", "2.0.0", False),
    ],
)
def test_satisfies_comparisons(installed, spec, expected):
    assert satisfies(installed, spec) is expected


@pytest.mark.parametrize(
    "installed,spec,expected",
    [
        ("4.18.0", "^4.17.0", True),   # caret: same major, >= floor
        ("4.17.0", "^4.17.0", True),
        ("5.0.0", "^4.17.0", False),   # caret: major bumped
        ("4.16.0", "^4.17.0", False),  # caret: below floor
        ("1.2.9", "~1.2.0", True),     # tilde: same major.minor
        ("1.3.0", "~1.2.0", False),    # tilde: minor bumped
        ("1.4.7", "~=1.4.5", True),    # PEP 440: same major.minor, >= floor
        ("1.5.0", "~=1.4.5", False),   # PEP 440: minor bumped
        ("2.5.0", "~=2.2", True),      # PEP 440 two-component: same major
        ("3.0.0", "~=2.2", False),
    ],
)
def test_satisfies_ranges(installed, spec, expected):
    assert satisfies(installed, spec) is expected


def test_satisfies_unparseable_is_none():
    assert satisfies("garbage", ">=1.0.0") is None
    assert satisfies("1.0.0", ">=garbage") is None


def test_satisfies_non_string_spec_is_true():
    assert satisfies("1.0.0", None) is True  # type: ignore[arg-type]
