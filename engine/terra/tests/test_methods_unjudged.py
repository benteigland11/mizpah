"""Two probes emitting the same quantity over DIFFERENT populations look
like corroboration to Terra, which matches on the quantity NAME alone.

Observed live: `n_stale` from two probes with different denominators gave
methods=2 and a reported value of 3.5 — the mean of 7 and 0. A number
describing nothing, and silent, because with no tolerance `agree` is None.
"""
from __future__ import annotations

from terra.corroboration import compute_corroboration, methods_unjudged


def _stats(by_probe, tolerance=None):
    return {
        "corroboration": compute_corroboration(
            by_probe, map_type="number", tolerance=tolerance
        )
    }


FAR_APART = {"a": {"n": 1, "mean": 7.0}, "b": {"n": 1, "mean": 0.0}}


def test_two_methods_far_apart_with_no_tolerance_is_flagged():
    s = _stats(FAR_APART)
    corr = s["corroboration"]
    assert corr["methods"] == 2
    assert corr["agree"] is None          # cannot judge without a tolerance
    assert methods_unjudged(s) is True


def test_declaring_a_tolerance_makes_it_judgeable_not_unjudged():
    """The prescribed fix must clear the flag — and here it should judge
    DISAGREEMENT, which is a different, louder state."""
    s = _stats(FAR_APART, tolerance="5%")
    assert s["corroboration"]["agree"] is False
    assert methods_unjudged(s) is False


def test_close_methods_are_not_flagged():
    """CAN-FAIL: two methods that nearly agree must not trip this, or every
    honest corroboration gets nagged."""
    s = _stats({"a": {"n": 1, "mean": 10.0}, "b": {"n": 1, "mean": 10.2}})
    assert methods_unjudged(s) is False


def test_single_method_is_not_flagged():
    s = _stats({"a": {"n": 3, "mean": 7.0}})
    assert s["corroboration"]["methods"] == 1
    assert methods_unjudged(s) is False
