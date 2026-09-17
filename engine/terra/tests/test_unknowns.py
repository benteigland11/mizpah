"""Unknowns: named gaps, hard validate, no silent resolve."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.unknowns import (
    create_unknown,
    link_probe,
    list_unknowns,
    set_status,
    validate_all_unknowns,
)


def test_create_and_list(tmp_path: Path):
    path = create_unknown(
        tmp_path,
        "mob_api",
        claim="How do we list hostiles in a region?",
        evidence_needed="probe dump of mobs or command surface",
    )
    assert path.is_file()
    rows = list_unknowns(tmp_path)
    assert len(rows) == 1
    assert rows[0]["ok"] is True
    assert rows[0]["record"]["status"] == "open"
    assert rows[0]["record"]["blocks_build"] is True


def test_empty_claim_fails():
    with pytest.raises(ValueError):
        create_unknown(Path("/tmp"), "x", claim="  ")


def test_resolved_without_trail_fails(tmp_path: Path):
    create_unknown(tmp_path, "gap", claim="what is X?", evidence_needed="a reading")
    with pytest.raises(ValueError, match="resolved"):
        set_status(tmp_path, "gap", "resolved")


def test_resolved_with_trail_ok(tmp_path: Path):
    create_unknown(tmp_path, "gap", claim="what is X?", evidence_needed="a reading")
    rec = set_status(
        tmp_path,
        "gap",
        "resolved",
        resolved_by="ran probe env_snapshot; registry uses command Y",
    )
    assert rec["status"] == "resolved"
    assert validate_all_unknowns(tmp_path)["ok"] is True


def test_link_probe_sets_probing(tmp_path: Path):
    create_unknown(tmp_path, "gap", claim="what is X?", evidence_needed="a reading")
    rec = link_probe(tmp_path, "gap", "mobs_in_region")
    assert rec["status"] == "probing"
    assert rec["probe_id"] == "mobs_in_region"
    assert rec["probe_ids"] == ["mobs_in_region"]


def test_link_probe_keeps_legacy_primary(tmp_path: Path):
    """Re-link must not drop an existing probe_id when probe_ids was empty."""
    create_unknown(
        tmp_path,
        "gap",
        claim="what is X?",
        evidence_needed="a reading",
        probe_id="first",
    )
    # Simulate legacy record with primary only
    path = tmp_path / ".terra" / "map" / "unknowns" / "gap.json"
    import json

    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["probe_ids"] = []
    path.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    rec2 = link_probe(tmp_path, "gap", "second")
    assert rec2["probe_ids"] == ["first", "second"]
    assert rec2["probe_id"] == "second"
