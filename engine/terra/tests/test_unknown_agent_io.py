"""Unknown lifecycle defaults to the sealed agent-response envelope."""

from __future__ import annotations

import json
from pathlib import Path

from terra.cli import main
from terra.probe_init import init_probe
from terra.probe_run import run_probe


def _payload(capsys) -> dict:
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "success"
    assert "data" in payload
    return payload


def _write_boolean_probe(
    root: Path, *, initialize: bool = True, kind: str = "watch"
) -> None:
    if initialize:
        init_probe(root, "truth", purpose="controlled true reading")
    probe = root / ".terra" / "map" / "probes" / "truth" / "probe.py"
    probe.write_text(
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        f"KIND = {kind!r}\n"
        + ("DURATION_S = 0\n" if kind == "watch" else "")
        +
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'literal'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    return {'to': to, 'status': 'ok', 'artifacts': [],\n"
        "            'measures': [{'quantity': 'truth', 'value': True}]}\n",
        encoding="utf-8",
    )


def test_unknown_mutations_emit_agent_envelopes(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main([
        "unknown", "create", "truth", "--type", "boolean",
        "--quantity", "truth", "--claim", "Is it true?",
        "--evidence", "controlled probe reading",
    ]) == 0
    created = _payload(capsys)
    assert created["data"]["id"] == "truth"
    assert created["meta"]["surface"] == "terra.unknown.create"
    assert created["data"]["next_actions"]

    assert main(["unknown", "link-probe", "truth", "truth"]) == 0
    linked_probe = _payload(capsys)
    assert linked_probe["data"]["probe_ids"] == ["truth"]
    assert linked_probe["meta"]["surface"] == "terra.unknown.link_probe"

    _write_boolean_probe(tmp_path)
    run_id = run_probe(tmp_path, "truth", to={"kind": "literal"})["id"]
    assert main(["unknown", "link-run", "truth", run_id]) == 0
    linked_run = _payload(capsys)
    assert linked_run["data"]["run_ids"] == [run_id]
    assert linked_run["meta"]["surface"] == "terra.unknown.link_run"

    assert main([
        "unknown", "status", "truth", "resolved",
        "--resolved-by", f"run:{run_id}",
    ]) == 0
    status = _payload(capsys)
    assert status["data"]["status"] == "resolved"
    assert status["meta"]["surface"] == "terra.unknown.status"
    assert "terra unknown graduate truth" in status["meta"]["note"]


def test_unknown_create_error_is_agent_envelope(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    argv = [
        "unknown", "create", "gap", "--claim", "What?",
        "--evidence", "A reading",
    ]
    assert main(argv) == 0
    _payload(capsys)

    assert main(argv) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "unknown_create"


def test_core_evidence_workflow_defaults_to_agent_envelopes(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)

    assert main(["init"]) == 0
    initialized = _payload(capsys)
    assert initialized["meta"]["surface"] == "terra.init"
    assert initialized["data"]["active_map"] == "global"

    assert main([
        "probe", "create", "truth", "--purpose",
        "controlled true reading", "--kind", "run",
    ]) == 0
    probe = _payload(capsys)
    assert probe["meta"]["surface"] == "terra.probe.create"
    assert probe["data"]["id"] == "truth"
    assert probe["data"]["next_actions"][0]["op"] == "probe.validate"

    assert main([
        "unknown", "create", "truth", "--type", "boolean",
        "--quantity", "truth", "--claim", "Is it true?",
        "--evidence", "controlled probe reading", "--probe", "truth",
    ]) == 0
    _payload(capsys)

    _write_boolean_probe(tmp_path, initialize=False, kind="run")
    run_id = run_probe(tmp_path, "truth", to={"kind": "literal"})["id"]
    assert main(["unknown", "link-run", "truth", run_id]) == 0
    _payload(capsys)

    assert main(["unknown", "graduate", "truth"]) == 0
    graduated = _payload(capsys)
    assert graduated["meta"]["surface"] == "terra.unknown.graduate"
    assert graduated["data"]["id"] == "truth"
    assert graduated["data"]["resolved_unknown_ids"] == ["truth"]
    assert graduated["data"]["next_actions"]
