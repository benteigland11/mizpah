import pytest

from src.version_bump_policy import (
    InvalidVersion,
    Version,
    classify_bump,
    compare_versions,
    is_republish,
    parse_version,
)


# --- parse_version ---


def test_parse_basic_triple():
    v = parse_version("1.2.3")
    assert v == Version(1, 2, 3)


def test_parse_zero_versions():
    v = parse_version("0.0.0")
    assert v == Version(0, 0, 0)


def test_parse_with_prerelease():
    v = parse_version("1.0.0-alpha.1")
    assert v.prerelease == ("alpha", "1")


def test_parse_with_build_metadata():
    v = parse_version("1.0.0+exp.sha.5114f85")
    assert v.build == ("exp", "sha", "5114f85")


def test_parse_with_prerelease_and_build():
    v = parse_version("1.0.0-rc.1+build.1")
    assert v.prerelease == ("rc", "1")
    assert v.build == ("build", "1")


def test_parse_rejects_bad_format():
    with pytest.raises(InvalidVersion):
        parse_version("1.2")
    with pytest.raises(InvalidVersion):
        parse_version("1.2.3.4")
    with pytest.raises(InvalidVersion):
        parse_version("v1.2.3")


def test_parse_rejects_leading_zeros():
    with pytest.raises(InvalidVersion):
        parse_version("01.2.3")


def test_parse_rejects_non_string():
    with pytest.raises(InvalidVersion):
        parse_version(1)


def test_is_prerelease_property():
    assert parse_version("1.0.0-alpha").is_prerelease is True
    assert parse_version("1.0.0").is_prerelease is False


# --- compare_versions ---


def test_compare_equal():
    assert compare_versions("1.2.3", "1.2.3") == 0


def test_compare_major_minor_patch():
    assert compare_versions("1.0.0", "2.0.0") == -1
    assert compare_versions("1.1.0", "1.0.0") == 1
    assert compare_versions("1.0.1", "1.0.0") == 1


def test_compare_prerelease_lower_than_release():
    assert compare_versions("1.0.0-alpha", "1.0.0") == -1
    assert compare_versions("1.0.0", "1.0.0-alpha") == 1


def test_compare_prerelease_numeric_vs_alphanumeric():
    # 1.0.0-1 < 1.0.0-alpha because numeric sorts below alphanumeric
    assert compare_versions("1.0.0-1", "1.0.0-alpha") == -1


def test_compare_prerelease_numeric_ordering():
    assert compare_versions("1.0.0-alpha.1", "1.0.0-alpha.2") == -1
    assert compare_versions("1.0.0-alpha.2", "1.0.0-alpha.10") == -1


def test_compare_prerelease_chain_length():
    # shorter chain sorts below longer when prefix matches
    assert compare_versions("1.0.0-alpha", "1.0.0-alpha.1") == -1


def test_compare_ignores_build_metadata():
    assert compare_versions("1.0.0+build.1", "1.0.0+build.2") == 0


# --- classify_bump ---


def test_classify_same():
    assert classify_bump("1.0.0", "1.0.0") == "same"


def test_classify_patch():
    assert classify_bump("1.2.3", "1.2.4") == "patch"


def test_classify_minor():
    assert classify_bump("1.2.3", "1.3.0") == "minor"


def test_classify_minor_resets_patch():
    assert classify_bump("1.2.3", "1.3.5") == "minor"


def test_classify_major():
    assert classify_bump("1.2.3", "2.0.0") == "major"


def test_classify_downgrade():
    assert classify_bump("2.0.0", "1.9.9") == "downgrade"
    assert classify_bump("1.0.0", "1.0.0-alpha") == "downgrade"


def test_classify_treats_build_only_change_as_same():
    assert classify_bump("1.0.0+a", "1.0.0+b") == "same"


def test_classify_prerelease_to_release_is_patch():
    # 1.0.0-alpha -> 1.0.0 is forward motion at same major/minor/patch core,
    # so it's classified as 'patch' (the smallest non-trivial bump).
    # The is_prerelease metadata is preserved on the Version object for
    # callers who need finer-grained policy.
    cur = parse_version("1.0.0-alpha")
    nxt = parse_version("1.0.0")
    assert (cur.major, cur.minor, cur.patch) == (nxt.major, nxt.minor, nxt.patch)
    assert classify_bump("1.0.0-alpha", "1.0.0") == "patch"


# --- is_republish ---


def test_is_republish_true_at_identical():
    assert is_republish("1.0.0", "1.0.0") is True


def test_is_republish_false_for_any_bump():
    assert is_republish("1.0.0", "1.0.1") is False
    assert is_republish("1.0.1", "1.0.0") is False
