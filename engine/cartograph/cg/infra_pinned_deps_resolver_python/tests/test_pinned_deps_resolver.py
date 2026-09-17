import pytest

from src.pinned_deps_resolver import (
    InvalidPinSpec,
    ResolvedPin,
    all_ok,
    partition,
    resolve_pin,
    resolve_pins,
)


def _lookup_from_map(versions: dict[str, str]):
    def _lookup(pin_id: str) -> str | None:
        return versions.get(pin_id)
    return _lookup


def test_resolve_pin_ok_when_versions_match():
    lookup = _lookup_from_map({"a": "1.0.0"})
    r = resolve_pin({"id": "a", "version": "1.0.0"}, lookup)
    assert r == ResolvedPin(id="a", pinned="1.0.0", found="1.0.0", state="ok")


def test_resolve_pin_version_mismatch():
    lookup = _lookup_from_map({"a": "1.2.0"})
    r = resolve_pin({"id": "a", "version": "1.0.0"}, lookup)
    assert r.state == "version-mismatch"
    assert r.found == "1.2.0"


def test_resolve_pin_missing():
    lookup = _lookup_from_map({})
    r = resolve_pin({"id": "a", "version": "1.0.0"}, lookup)
    assert r.state == "missing"
    assert r.found is None


def test_resolve_pin_ignores_extra_keys():
    lookup = _lookup_from_map({"a": "1.0.0"})
    r = resolve_pin(
        {"id": "a", "version": "1.0.0", "language": "python", "note": "x"},
        lookup,
    )
    assert r.state == "ok"


def test_resolve_pin_rejects_missing_id():
    with pytest.raises(InvalidPinSpec):
        resolve_pin({"version": "1.0.0"}, _lookup_from_map({}))


def test_resolve_pin_rejects_empty_id():
    with pytest.raises(InvalidPinSpec):
        resolve_pin({"id": "", "version": "1.0.0"}, _lookup_from_map({}))


def test_resolve_pin_rejects_missing_version():
    with pytest.raises(InvalidPinSpec):
        resolve_pin({"id": "a"}, _lookup_from_map({}))


def test_resolve_pin_rejects_non_string_version():
    with pytest.raises(InvalidPinSpec):
        resolve_pin({"id": "a", "version": 1}, _lookup_from_map({}))


def test_resolve_pins_preserves_order():
    lookup = _lookup_from_map({"a": "1.0.0", "b": "2.0.0", "c": "3.0.0"})
    pins = [
        {"id": "c", "version": "3.0.0"},
        {"id": "a", "version": "1.0.0"},
        {"id": "b", "version": "2.0.0"},
    ]
    resolved = resolve_pins(pins, lookup)
    assert [r.id for r in resolved] == ["c", "a", "b"]


def test_resolve_pins_mixed_states():
    lookup = _lookup_from_map({"a": "1.0.0", "b": "9.9.9"})
    pins = [
        {"id": "a", "version": "1.0.0"},
        {"id": "b", "version": "1.0.0"},
        {"id": "c", "version": "1.0.0"},
    ]
    resolved = resolve_pins(pins, lookup)
    states = [r.state for r in resolved]
    assert states == ["ok", "version-mismatch", "missing"]


def test_partition_groups_by_state():
    lookup = _lookup_from_map({"a": "1.0.0", "b": "9.9.9"})
    pins = [
        {"id": "a", "version": "1.0.0"},
        {"id": "b", "version": "1.0.0"},
        {"id": "c", "version": "1.0.0"},
    ]
    grouped = partition(resolve_pins(pins, lookup))
    assert [r.id for r in grouped["ok"]] == ["a"]
    assert [r.id for r in grouped["version-mismatch"]] == ["b"]
    assert [r.id for r in grouped["missing"]] == ["c"]


def test_all_ok_true_when_every_pin_resolves():
    lookup = _lookup_from_map({"a": "1.0.0", "b": "2.0.0"})
    pins = [
        {"id": "a", "version": "1.0.0"},
        {"id": "b", "version": "2.0.0"},
    ]
    assert all_ok(resolve_pins(pins, lookup)) is True


def test_all_ok_false_with_any_break():
    lookup = _lookup_from_map({"a": "1.0.0"})
    pins = [
        {"id": "a", "version": "1.0.0"},
        {"id": "b", "version": "2.0.0"},
    ]
    assert all_ok(resolve_pins(pins, lookup)) is False


def test_lookup_callable_can_be_arbitrary():
    """Resolver accepts any callable - composed lookups work without changes."""
    calls: list[str] = []

    def lookup(pin_id: str) -> str | None:
        calls.append(pin_id)
        return "1.0.0" if pin_id.startswith("ok-") else None

    pins = [
        {"id": "ok-a", "version": "1.0.0"},
        {"id": "missing-b", "version": "1.0.0"},
    ]
    resolved = resolve_pins(pins, lookup)
    assert calls == ["ok-a", "missing-b"]
    assert [r.state for r in resolved] == ["ok", "missing"]


def test_empty_pin_list_returns_empty_list():
    assert resolve_pins([], _lookup_from_map({})) == []
    assert all_ok([]) is True
