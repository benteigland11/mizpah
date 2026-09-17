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
"""Terra map probe: {purpose}

kind={kind}{duration_note}

Level-1 contract:
  input  → ctx["to"] (target)
  output → {{"to", "status", "artifacts"}}
Substrate (later runs) stamps time/from — do not rely on probe for those.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PURPOSE = {purpose!r}
KIND = {kind!r}
{duration_decl}
REQUIRED_EXPORTS = {exports!r}


def run(ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Survey the world ({kind}{mode_hint}).

    Level-1: accept `to`, return non-empty `to`, string `status`, list `artifacts`.
    Honor dry_run / level1 (never wait).

    Watch window (probe owns it — substrate does not re-call run):
      ctx["watch_mode"] == "snapshot"  → single shot
      ctx["watch_mode"] == "window"    → poll until ctx["deadline_unix"]
    """
    ctx = ctx or {{}}
    to = ctx.get("to")
    if not to:
        to = {{"kind": "unspecified", "note": "set a real target"}}

    if ctx.get("dry_run") or ctx.get("_terra_validation") == "level1":
        return {{
            "to": to,
            "status": "ok",
            "artifacts": [],
        }}

    # TODO: implement {kind} survey against `to`.
    # If watch_mode == "window", loop until time.time() >= ctx["deadline_unix"].
    # Real runs must produce at least one artifact file (map evidence bar).
    out = Path(__file__).resolve().parent / "_last_reading.txt"
    out.write_text(json.dumps({{"to": to, "note": "scaffold stub"}}, indent=2) + "\\n")
    # Prefer recommended status vocab: ok|degraded|unavailable|empty|error
    # Optional measures feed number-typed knowns/unknowns:
    #   "measures": [{{"quantity": "hostile_count", "value": 3}}]
    return {{
        "to": to,
        "status": "ok",
        "artifacts": [
            {{"path": str(out), "role": "summary"}},
        ],
        "measures": [],
    }}


if __name__ == "__main__":
    import json
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
