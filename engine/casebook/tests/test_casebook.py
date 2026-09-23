import json

import pytest

from casebook import cases, cli, ops


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return tmp_path / "casebook" / "cases"


def tempo_case(**over):
    base = dict(problem="The bar after a slowed bar still reads at the slower tempo, or the closing bar reads at body speed",
                diagnosis="A MIDI tempo event holds until the next tempo event; it does not reset at the barline",
                fix="Write a tempo event at the downbeat of every bar whose speed differs from the bar before",
                item=cases.instance("romantic_piano/shape_rubato", "closing_stretch_ratio reads 1.0", "added set_tempo at bar 9", 6))
    base.update(over)
    return ops.add(base["problem"], base["diagnosis"], base["fix"], base["item"], touches=["midi"])


def test_add_then_search_by_the_symptom():
    assert tempo_case()["ok"]
    hits = ops.search("closing bar reads at body speed tempo")["hits"]
    assert hits and hits[0]["diagnosis"].startswith("A MIDI tempo event")
    assert hits[0]["example"]["symptom"] == "closing_stretch_ratio reads 1.0"
    assert hits[0]["happened"] == 1


def test_general_fields_refuse_what_belongs_to_one_episode():
    out = tempo_case(problem="piece.mid reads closing_stretch_ratio at 1.0 in /work/out/piece.mid")
    assert not out["ok"]
    joined = " ".join(out["refused"])
    assert "piece.mid" in joined and "closing_stretch_ratio" in joined


def test_the_fix_may_name_the_command_that_did_it():
    assert tempo_case(fix="Call mido.MetaMessage('set_tempo') at each changed bar")["ok"]


def test_a_case_needs_its_episode():
    out = tempo_case(item=cases.instance("", ""))
    assert not out["ok"] and any("where missing" in r for r in out["refused"])


def test_a_lookalike_diagnosis_is_sent_back_then_joined():
    assert tempo_case()["ok"]
    again = tempo_case(problem="Only the first slowed bar is slow; the next bar comes back to speed early",
                       item=cases.instance("nocturne/phrase", "peak_stretch ok, close ratio 1.02"))
    assert not again["ok"] and again["similar"]
    first = again["similar"][0]["id"]
    joined = ops.add("", "", "", cases.instance("nocturne/phrase", "close ratio 1.02"), into=first)
    assert joined["ok"] and joined["happened"] == 2


def test_new_files_a_different_diagnosis_anyway():
    assert tempo_case()["ok"]
    out = ops.add("Tempo reads right in ticks and wrong in seconds",
                  "A MIDI tempo event holds until the next tempo event; it does not reset at the barline",
                  "Read seconds from the tempo map", cases.instance("x/y", "seconds disagree"), new=True)
    assert out["ok"] and out["id"] != "the-bar-after-a-slowed-bar-still-reads-at"


def test_cli_round_trip(capsys):
    code = cli.main(["add", "--problem", "The gate refuses a true reading as vacuous",
                     "--diagnosis", "A claim about every member of an empty set is refused until members exist",
                     "--fix", "Build the things it quantifies over first, then measure",
                     "--where", "sketch/house", "--symptom", "reads true while count reads 0: vacuous", "--touches", "terra gate"])
    assert code == 0
    capsys.readouterr()
    cli.main(["search", "vacuous", "universal"])
    out = json.loads(capsys.readouterr().out)
    assert out["hits"][0]["touches"] == ["terra gate"]
    assert cli.main(["validate"]) == 0
