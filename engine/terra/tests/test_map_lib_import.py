"""Probes can import shared helpers from .terra/map/lib."""

from __future__ import annotations

from pathlib import Path

from terra.paths import ensure_map_store, map_lib_root
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.probe_validate import validate_probe_dir


def test_probe_imports_map_lib(tmp_path: Path):
    ensure_map_store(tmp_path)
    lib = map_lib_root(tmp_path)
    (lib / "shared_helper.py").write_text(
        "def tag():\n    return 'from-map-lib'\n",
        encoding="utf-8",
    )
    pdir = init_probe(tmp_path, "uses_lib", purpose="import shared helper")
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\nDURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "PURPOSE = 'import shared helper'\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run') or ctx.get('_terra_validation') == 'level1':\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    from shared_helper import tag\n"
        "    return {\n"
        "        'to': to,\n"
        "        'status': tag(),\n"
        "        'artifacts': [{'role': 'note', 'path': 'n/a'}],\n"
        "    }\n",
        encoding="utf-8",
    )
    result = validate_probe_dir(pdir)
    assert result["ok"] is True, result["blocks"]

    stamp = run_probe(tmp_path, "uses_lib", to={"kind": "lib-test"})
    assert stamp["status"] == "from-map-lib"
