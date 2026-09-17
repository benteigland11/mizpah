"""Recommended status vocabulary — warn-only + list filter."""

from __future__ import annotations

from pathlib import Path

from terra.probe_init import init_probe
from terra.probe_run import list_runs, run_probe
from terra.status_vocab import warn_status_vocab


def test_warn_custom_status():
    w = warn_status_vocab("missing_layout", live=True)
    assert any("freeform" in x or "recommended" in x for x in w)


def test_no_warn_recommended():
    assert warn_status_vocab("ok", live=True) == []
    assert warn_status_vocab("unavailable", live=True) == []


def test_list_filter_status(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    # force custom status via overwrite
    pdir = tmp_path / ".terra" / "map" / "probes" / "p"
    (pdir / "probe.py").write_text(
        "KIND='watch'\nDURATION_S=0\n"
        "REQUIRED_EXPORTS=['to','status','artifacts']\n"
        "from pathlib import Path\n"
        "def run(ctx=None):\n"
        "    ctx=ctx or {}\n"
        "    to=ctx.get('to') or {'kind':'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to':to,'status':'ok','artifacts':[]}\n"
        "    p=Path(__file__).parent/'o.txt'\n"
        "    p.write_text('x')\n"
        "    return {'to':to,'status':'unavailable',"
        "{'artifacts':[{'path':str(p),'role':'out'}]}\n".replace(
            "{'artifacts'", "'artifacts':"
        ),
        encoding="utf-8",
    )
    # fix the botched string - rewrite cleanly
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\nDURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "from pathlib import Path\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    p = Path(__file__).parent / 'o.txt'\n"
        "    p.write_text('x')\n"
        "    return {\n"
        "        'to': to,\n"
        "        'status': 'unavailable',\n"
        "        'artifacts': [{'path': str(p), 'role': 'out'}],\n"
        "    }\n",
        encoding="utf-8",
    )
    stamp = run_probe(tmp_path, "p", to={"kind": "server"})
    assert stamp["status"] == "unavailable"
    rows = list_runs(tmp_path, status="unavailable")
    assert len(rows) == 1
    assert list_runs(tmp_path, status="ok") == []
