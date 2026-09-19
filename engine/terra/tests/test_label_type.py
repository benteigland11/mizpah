"""label: a categorical reading — mode is the value, agreement is the bar."""
from terra.number_type import compute_label_stats, derive_confidence_label, extract_label_measures_from_run_meta
from terra.corroboration import compute_corroboration
from terra.readings import extract_value


def test_label_stats_mode_and_agreement():
    st = compute_label_stats(["S03", "S03", "S07", "S03"])
    assert st["n"] == 4 and st["mode"] == "S03" and st["distinct"] == 2 and st["agreement"] == 0.75
    assert compute_label_stats([])["mode"] is None


def test_label_confidence_needs_unanimity():
    assert derive_confidence_label(compute_label_stats(["a"])) == "low"
    assert derive_confidence_label(compute_label_stats(["a", "a", "a"])) == "med"
    assert derive_confidence_label(compute_label_stats(["a", "a", "b"])) == "low"


def test_label_measures_are_strings_only():
    meta = {"measures": [{"quantity": "best", "value": "2025-W13.csv"}, {"quantity": "best", "value": 3},
                         {"quantity": "other", "value": "x"}, {"quantity": "best", "value": True}]}
    assert extract_label_measures_from_run_meta(meta, quantity="best") == ["2025-W13.csv"]


def test_label_corroboration_compares_modes():
    by_probe = {"p1": compute_label_stats(["a", "a"]), "p2": compute_label_stats(["b"])}
    assert compute_corroboration(by_probe, map_type="label")["agree"] is False
    by_probe["p2"] = compute_label_stats(["a"])
    assert compute_corroboration(by_probe, map_type="label")["agree"] is True


def test_extract_value_is_mode():
    assert extract_value({"type": "label", "stats": compute_label_stats(["S03", "S03"])}) == "S03"
