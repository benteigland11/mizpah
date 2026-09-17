"""Rust engine unit tests that don't need the cargo toolchain.

These guard logic that has bitten us before, so they run in CI on every
platform regardless of whether Rust is installed.
"""

import os

from cartograph.languages.rust import RustEngine


def test_coverage_ignore_regex_excludes_only_tests_examples_for_a_widget(tmp_path):
    """Regression: a normal widget installs at cg/<id>/src/lib.rs, so excluding
    'cg' from the coverage denominator matched the widget's OWN source via its
    install path and reported a false 0% coverage. A non-blueprint path must
    exclude only tests/ and examples/ - never cg/."""
    widget = tmp_path / "cg" / "universal-thing-rust"
    (widget / "src").mkdir(parents=True)
    regex = RustEngine._coverage_ignore_regex(str(widget))
    assert "cg" not in regex
    assert regex == r"(tests|examples)[/\\]"


def test_coverage_ignore_regex_excludes_cg_for_a_blueprint(tmp_path):
    """A blueprint sandbox carries dep widgets under cg/ that must not count
    toward the blueprint's own coverage. Detected by blueprint.json, not path."""
    bp = tmp_path / "bp-thing-rust"
    bp.mkdir()
    (bp / "blueprint.json").write_text("{}")
    regex = RustEngine._coverage_ignore_regex(str(bp))
    assert regex == r"(tests|examples|cg)[/\\]"


def test_parse_coverage_reads_llvm_cov_json():
    """The total line percent comes from data[0].totals.lines.percent."""
    payload = '{"data":[{"totals":{"lines":{"percent":87.5}}}]}'
    assert RustEngine._parse_coverage(payload) == 87.5
    assert RustEngine._parse_coverage("") is None
    assert RustEngine._parse_coverage("not json") is None
