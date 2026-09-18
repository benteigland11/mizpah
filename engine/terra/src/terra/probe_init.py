"""Create a new Python probe package under .terra/map/probes/."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .paths import ensure_probes_store, probe_dir
from .probe_contract import (
    DEFAULT_WATCH_DURATION_S,
    PROBE_ENTRY_DEFAULT,
    PROBE_KINDS,
    PROBE_LANGUAGE,
    PROBE_META_NAME,
    PROBE_RESULT_KEYS,
    PROBE_SCHEMA_VERSION,
    PROBE_SCRIPT_NAME,
    watch_mode_label,
)

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_PROBE_PY_TEMPLATE = '''\
\"\"\"Terra map probe: {purpose}

kind={kind}{duration_note}

Implement `measure` and nothing else: write it as `measure.py` next to this file
(`def measure(ctx): ...` returning {{quantity: value}}); `run` below imports it when it
exists and carries the level-1 contract (input ctx["to"]; output "to", "status",
"artifacts", plus "measures"). Nothing in this file needs editing.
Substrate (later runs) stamps time/from — do not rely on probe for those.
\"\"\"

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PURPOSE = {purpose!r}
KIND = {kind!r}
{duration_decl}
REQUIRED_EXPORTS = {exports!r}


def measure(ctx: dict[str, Any]) -> dict[str, Any]:
    \"\"\"Read the world and return the readings as {{quantity: value}}.

    Called only on real runs, never on dry_run or level-1 validation. ctx["to"] is the
    target; ctx["inputs"] holds declared map inputs. Spell each quantity exactly as the
    unknown it feeds. For kind=watch with ctx["watch_mode"] == "window", poll until
    time.time() >= ctx["deadline_unix"] before returning.

    Preferred: create `measure.py` beside this file defining `measure(ctx)`; it takes
    precedence over this stub. Editing the stub in place works too.
    \"\"\"
    raise NotImplementedError("TODO: implement measure()")  # scaffold stub


def _resolve_measure():
    \"\"\"measure.py beside the probe wins over the stub above; both share this contract.\"\"\"
    sibling = Path(__file__).resolve().parent / "measure.py"
    if sibling.is_file():
        import importlib.util

        spec = importlib.util.spec_from_file_location("terra_probe_measure_" + Path(__file__).resolve().parent.name, sibling)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not callable(getattr(module, "measure", None)):
            raise NotImplementedError("measure.py must define measure(ctx)")
        return module.measure
    return measure


def run(ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    \"\"\"Level-1 contract around measure(); do not edit.\"\"\"
    ctx = ctx or {{}}
    to = ctx.get("to") or {{"kind": "unspecified", "note": "set a real target"}}
    if ctx.get("dry_run") or ctx.get("_terra_validation") == "level1":
        return {{"to": to, "status": "ok", "artifacts": []}}
    try:
        readings = _resolve_measure()(ctx)
    except NotImplementedError as error:
        return {{"to": to, "status": "error", "artifacts": [], "measures": [], "error": str(error)}}
    if not isinstance(readings, dict) or not readings:
        return {{"to": to, "status": "error", "artifacts": [], "measures": [],
                "error": "measure() must return a non-empty {{quantity: value}} dict"}}
    # Real runs must produce at least one artifact file (map evidence bar).
    out = Path(__file__).resolve().parent / "_last_reading.json"
    out.write_text(json.dumps({{"to": to, "readings": readings}}, indent=2, default=str) + "\\n")
    return {{
        "to": to,
        "status": "ok",
        "artifacts": [{{"path": str(out), "role": "summary"}}],
        "measures": [{{"quantity": name, "value": value}} for name, value in readings.items()],
    }}


if __name__ == "__main__":
    import sys

    print(json.dumps(run({{"to": {{"kind": "cli"}}}}), indent=2))
    sys.exit(0)
'''


def init_probe(
    project_root: Path,
    probe_id: str,
    *,
    purpose: str,
    kind: str = "watch",
    duration_s: float | None = None,
    force: bool = False,
    inputs: dict[str, str] | None = None,
    measures: list[str] | None = None,
) -> Path:
    if not _SLUG_RE.match(probe_id):
        raise ValueError(
            f"probe id {probe_id!r} must match {_SLUG_RE.pattern} "
            "(e.g. env_versions, server_region_x)"
        )
    if not purpose or not str(purpose).strip():
        raise ValueError(
            "purpose is required (one sentence: what mystery this reduces)"
        )
    if kind not in PROBE_KINDS:
        raise ValueError(
            f"kind must be one of {sorted(PROBE_KINDS)}, got {kind!r}"
        )

    if kind == "run":
        if duration_s is not None:
            raise ValueError(
                "duration_s is only for kind=watch (0=snapshot, >0=stream window)"
            )
        duration_s_val = None
    else:
        duration_s_val = (
            DEFAULT_WATCH_DURATION_S if duration_s is None else float(duration_s)
        )
        if duration_s_val < 0:
            raise ValueError("duration_s must be >= 0")

    ensure_probes_store(project_root)
    pdir = probe_dir(project_root, probe_id)
    if pdir.exists() and not force:
        if any(pdir.iterdir()):
            raise FileExistsError(
                f"probe already exists: {pdir} (pass force=True to overwrite)"
            )
    pdir.mkdir(parents=True, exist_ok=True)

    meta: dict = {
        "schema_version": PROBE_SCHEMA_VERSION,
        "id": probe_id,
        "purpose": purpose.strip(),
        "language": PROBE_LANGUAGE,
        "entry": PROBE_ENTRY_DEFAULT,
        "kind": kind,
    }
    if inputs:
        from .map_inputs import validate_map_bindings

        blocks = validate_map_bindings(inputs)
        if blocks:
            raise ValueError("invalid probe inputs: " + "; ".join(blocks))
        meta["inputs"] = inputs
    if measures:
        # The instrument's declared quantities. A run that reports anything else is refused,
        # so a probe cannot quietly grow into one that measures everything.
        names = [str(m).strip() for m in measures if str(m).strip()]
        if any(not _SLUG_RE.match(n) for n in names):
            raise ValueError("measures must be slug names matching ^[a-z][a-z0-9_]*$")
        meta["measures"] = names
    if kind == "watch":
        meta["duration_s"] = duration_s_val

    (pdir / PROBE_META_NAME).write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    exports = sorted(PROBE_RESULT_KEYS)
    if kind == "watch":
        assert duration_s_val is not None
        duration_decl = f"DURATION_S = {duration_s_val!r}  # 0 = snapshot"
        duration_note = f", duration_s={duration_s_val} ({watch_mode_label(duration_s_val)})"
        mode_hint = f", {watch_mode_label(duration_s_val)}"
    else:
        duration_decl = ""
        duration_note = ""
        mode_hint = ""

    script = _PROBE_PY_TEMPLATE.format(
        purpose=purpose.strip(),
        exports=exports,
        kind=kind,
        duration_decl=duration_decl,
        duration_note=duration_note,
        mode_hint=mode_hint,
    )
    script_path = pdir / PROBE_SCRIPT_NAME
    if script_path.exists() and not force:
        pass
    else:
        script_path.write_text(script, encoding="utf-8")

    return pdir
