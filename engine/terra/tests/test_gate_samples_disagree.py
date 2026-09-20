"""A count does not average: integer samples of one number known that disagree fail the gate."""
from __future__ import annotations

from pathlib import Path

from terra.gate import check_gate
from terra.knowns import graduate_unknown, link_run_known
from terra.paths import ensure_project_root
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.unknowns import create_unknown, link_run


def _probe(root: Path, pid: str, quantity: str, value: float) -> None:
    (root/'.terra'/'map'/'probes'/pid/'probe.py').write_text(
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\nKIND = 'watch'\nDURATION_S = 0\n"
        "def run(ctx=None):\n    ctx = ctx or {}\n    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        f"    return {{'to': to, 'status': 'ok', 'artifacts': [], 'measures': [{{'quantity': {quantity!r}, 'value': {value!r}}}]}}\n")


def _known(root: Path, kid: str, values: list[float]) -> None:
    pid = 'p_'+kid
    init_probe(root, pid, purpose='p')
    create_unknown(root, 'u_'+kid, claim='?', evidence_needed='e', map_type='number', quantity=kid)
    for i, value in enumerate(values):
        _probe(root, pid, kid, value)
        rid = run_probe(root, pid, to={'kind': 'region'}).get('id')
        if i == 0:
            link_run(root, 'u_'+kid, rid)
            graduate_unknown(root, 'u_'+kid, known_id=kid)
        else:
            link_run_known(root, kid, rid)


def test_integer_samples_that_disagree_fail_and_agreeing_or_real_valued_ones_pass(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    ensure_project_root(tmp_path)
    _known(tmp_path, 'letterforms', [0, 0, 3, 3])      # before and after the artifact changed: mean 1.5, read by nobody
    _known(tmp_path, 'rows', [3, 3, 3])                 # a count that agrees
    _known(tmp_path, 'ratio', [2.5, 2.75])              # a real-valued reading: spread is its uncertainty
    verdict = check_gate(tmp_path)
    kinds = {(v['id'], v['kind']) for v in verdict['violations']}
    assert ('letterforms', 'samples_disagree') in kinds
    assert ('rows', 'samples_disagree') not in kinds and ('ratio', 'samples_disagree') not in kinds
    why = next(v['why'] for v in verdict['violations'] if v['id'] == 'letterforms' and v['kind'] == 'samples_disagree')
    assert 'read 0, 3' in why and 'does not average' in why
