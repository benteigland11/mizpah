import pytest

from src.prefix_router import (
    DuplicatePrefixError,
    NoMatchError,
    PrefixRouter,
)


def test_resolve_picks_longest_prefix():
    r = PrefixRouter()
    r.register("a-", "short")
    r.register("a-b-", "long")
    assert r.resolve("a-b-thing") == "long"
    assert r.resolve("a-other") == "short"


def test_match_returns_prefix_and_handler():
    r = PrefixRouter()
    r.register("ns-", "h")
    assert r.match("ns-x") == ("ns-", "h")


def test_match_returns_none_when_no_prefix_matches():
    r = PrefixRouter()
    r.register("ns-", "h")
    assert r.match("other") is None


def test_resolve_uses_default_when_no_match():
    r = PrefixRouter(default="fallback")
    r.register("ns-", "h")
    assert r.resolve("nope") == "fallback"


def test_resolve_raises_when_no_match_and_no_default():
    r = PrefixRouter()
    r.register("ns-", "h")
    with pytest.raises(NoMatchError):
        r.resolve("nope")


def test_explicit_empty_prefix_beats_default():
    r = PrefixRouter(default="ctor-default")
    r.register("", "explicit-default")
    assert r.resolve("anything") == "explicit-default"


def test_duplicate_register_raises():
    r = PrefixRouter()
    r.register("a-", 1)
    with pytest.raises(DuplicatePrefixError):
        r.register("a-", 2)


def test_unregister_removes_prefix_and_returns_handler():
    r = PrefixRouter()
    r.register("a-", "h")
    assert r.unregister("a-") == "h"
    assert "a-" not in r


def test_unregister_unknown_prefix_raises_keyerror():
    r = PrefixRouter()
    with pytest.raises(KeyError):
        r.unregister("a-")


def test_prefixes_listed_longest_first():
    r = PrefixRouter()
    r.register("a-", 1)
    r.register("a-b-c-", 1)
    r.register("a-b-", 1)
    assert r.prefixes() == ["a-b-c-", "a-b-", "a-"]


def test_iter_yields_pairs_longest_first():
    r = PrefixRouter()
    r.register("a-", "x")
    r.register("a-b-", "y")
    assert list(r) == [("a-b-", "y"), ("a-", "x")]


def test_len_reflects_registrations():
    r = PrefixRouter()
    assert len(r) == 0
    r.register("a-", 1)
    r.register("b-", 2)
    assert len(r) == 2


def test_handlers_are_opaque_values():
    r = PrefixRouter()
    config = {"url": "https://example.test", "auth": "bearer"}
    r.register("cg-", config)
    assert r.resolve("cg-widget") is config


def test_register_rejects_non_string_prefix():
    r = PrefixRouter()
    with pytest.raises(TypeError):
        r.register(42, "h")


def test_match_rejects_non_string_key():
    r = PrefixRouter()
    with pytest.raises(TypeError):
        r.match(42)
