"""Python probe contract — level 1 bare minimum.

Probe owns:
  input  → to (target)
  output → to, status, artifacts

Substrate owns (not required from probe code at level 1):
  time, from

Either input or output failure is a hard, non-silent error (blocks).
"""

from __future__ import annotations

from typing import Any

PROBE_SCHEMA_VERSION = 1
PROBE_LANGUAGE = "python"
VALIDATION_LEVEL = 1

PROBE_RESULT_KEYS = frozenset({"to", "status", "artifacts"})
REQUIRED_EXPORT_KEYS = PROBE_RESULT_KEYS

# Instrument posture: drive the world vs observe it.
# Snapshot = watch with duration_s == 0 (or very small).
PROBE_KINDS = frozenset({"run", "watch"})
DEFAULT_WATCH_DURATION_S = 0.0  # 0 → snapshot (one-shot observe)

SUBSTRATE_KEYS = frozenset({"time", "from", "captured_at", "started_at", "finished_at"})

PROBE_META_NAME = "probe.json"
PROBE_SCRIPT_NAME = "probe.py"
PROBE_ENTRY_DEFAULT = "probe.py:run"

LEVEL1_FIXTURE_TO: dict[str, Any] = {
    "kind": "terra_level1_fixture",
    "note": "synthetic target for design-time validation only",
}

LEVEL1_CTX: dict[str, Any] = {
    "to": LEVEL1_FIXTURE_TO,
    "dry_run": True,
    "_terra_validation": "level1",
}

LEVEL1_RUN_TIMEOUT_S = 10.0

# Prefixes so failures are never ambiguous about which step broke
STEP_INPUT = "input"
STEP_OUTPUT = "output"
STEP_EXECUTE = "execute"


def is_nonempty_to(value: Any) -> bool:
    """True if `to` names a real survey target."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return len(value) > 0
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    if isinstance(value, (int, float, bool)):
        return True
    return bool(value)


def _step_block(step: str, message: str) -> str:
    return f"level1/{step}: {message}"


def validate_probe_input_level1(ctx: Any) -> list[str]:
    """Validate probe **input** before run. Failures are hard blocks (non-silent)."""
    blocks: list[str] = []

    if ctx is None:
        blocks.append(_step_block(STEP_INPUT, "ctx is required (got None)"))
        return blocks

    if not isinstance(ctx, dict):
        blocks.append(
            _step_block(
                STEP_INPUT,
                f"ctx must be a dict, got {type(ctx).__name__}",
            )
        )
        return blocks

    if "to" not in ctx:
        blocks.append(
            _step_block(
                STEP_INPUT,
                "missing required key 'to' (target the probe will survey)",
            )
        )
        return blocks

    if not is_nonempty_to(ctx["to"]):
        blocks.append(
            _step_block(
                STEP_INPUT,
                "ctx['to'] must be a non-empty target — empty/missing target is not allowed",
            )
        )

    return blocks


def validate_probe_output_level1(result: Any) -> list[str]:
    """Validate probe **output** after run. Failures are hard blocks (non-silent)."""
    blocks: list[str] = []

    if result is None:
        blocks.append(
            _step_block(
                STEP_OUTPUT,
                "run() returned None — must return a dict with to/status/artifacts",
            )
        )
        return blocks

    if not isinstance(result, dict):
        blocks.append(
            _step_block(
                STEP_OUTPUT,
                f"run() must return a dict, got {type(result).__name__}",
            )
        )
        return blocks

    missing = PROBE_RESULT_KEYS - frozenset(result.keys())
    if missing:
        blocks.append(
            _step_block(
                STEP_OUTPUT,
                f"missing keys {sorted(missing)} (need {sorted(PROBE_RESULT_KEYS)})",
            )
        )

    if "to" in result and not is_nonempty_to(result["to"]):
        blocks.append(
            _step_block(
                STEP_OUTPUT,
                "result['to'] must be a non-empty target (what the probe pointed at)",
            )
        )

    if "status" in result:
        status = result["status"]
        if not isinstance(status, str) or not status.strip():
            blocks.append(
                _step_block(
                    STEP_OUTPUT,
                    "result['status'] must be a non-empty string",
                )
            )

    if "artifacts" in result:
        arts = result["artifacts"]
        if not isinstance(arts, list):
            blocks.append(
                _step_block(
                    STEP_OUTPUT,
                    f"result['artifacts'] must be a list, got {type(arts).__name__}",
                )
            )
        else:
            for i, item in enumerate(arts):
                if item is None:
                    blocks.append(
                        _step_block(STEP_OUTPUT, f"artifacts[{i}] is null")
                    )
                elif isinstance(item, dict):
                    pass
                elif not isinstance(item, str):
                    blocks.append(
                        _step_block(
                            STEP_OUTPUT,
                            f"artifacts[{i}] must be str or dict, got {type(item).__name__}",
                        )
                    )

    return blocks


# Back-compat name used in early tests
def validate_probe_result_level1(result: Any) -> list[str]:
    return validate_probe_output_level1(result)


def normalize_duration_s(value: Any) -> tuple[float | None, str | None]:
    """Return (seconds, error). None seconds only when error is set."""
    if value is None:
        return DEFAULT_WATCH_DURATION_S, None
    if isinstance(value, bool):
        return None, "duration_s must be a number, not bool"
    if isinstance(value, (int, float)):
        if value < 0:
            return None, "duration_s must be >= 0 (0 = snapshot)"
        return float(value), None
    return None, f"duration_s must be a number, got {type(value).__name__}"


def watch_mode_label(duration_s: float) -> str:
    """Human label: snapshot when duration is zero (or tiny)."""
    if duration_s <= 0:
        return "snapshot"
    return "stream"


def validate_kind_meta(meta: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Validate kind (+ duration_s for watch) on probe.json.

    Returns (blocks, normalized_info).
    """
    blocks: list[str] = []
    info: dict[str, Any] = {
        "kind": None,
        "duration_s": None,
        "watch_mode": None,
    }

    kind = meta.get("kind")
    if kind is None:
        blocks.append(
            "level1: probe.json missing 'kind' "
            f"(must be one of {sorted(PROBE_KINDS)})"
        )
        return blocks, info
    if kind not in PROBE_KINDS:
        blocks.append(
            f"level1: kind {kind!r} invalid "
            f"(must be one of {sorted(PROBE_KINDS)})"
        )
        return blocks, info

    info["kind"] = kind

    if kind == "watch":
        dur, err = normalize_duration_s(meta.get("duration_s", DEFAULT_WATCH_DURATION_S))
        if err:
            blocks.append(f"level1: {err}")
        else:
            assert dur is not None
            info["duration_s"] = dur
            info["watch_mode"] = watch_mode_label(dur)
    elif "duration_s" in meta and meta["duration_s"] is not None:
        # run probes don't use duration — keep meta honest
        blocks.append(
            "level1: duration_s is only valid for kind=watch "
            "(snapshot/stream observe window); remove it for kind=run"
        )

    return blocks, info


def validate_kind_in_script(
    mod: Any,
    *,
    meta_kind: str | None,
    meta_duration_s: float | None,
) -> list[str]:
    """Probe script must declare KIND matching meta; watch needs DURATION_S."""
    blocks: list[str] = []

    script_kind = getattr(mod, "KIND", None)
    if script_kind is None:
        blocks.append(
            "level1: probe module must define KIND = 'run' | 'watch' "
            "(must match probe.json)"
        )
        return blocks

    if script_kind not in PROBE_KINDS:
        blocks.append(
            f"level1: KIND {script_kind!r} invalid "
            f"(must be one of {sorted(PROBE_KINDS)})"
        )
        return blocks

    if meta_kind is not None and script_kind != meta_kind:
        blocks.append(
            f"level1: KIND in script ({script_kind!r}) does not match "
            f"probe.json kind ({meta_kind!r})"
        )

    if script_kind == "watch":
        if not hasattr(mod, "DURATION_S"):
            blocks.append(
                "level1: kind=watch probe must define DURATION_S "
                "(0 = snapshot, >0 = watch window seconds)"
            )
        else:
            dur, err = normalize_duration_s(getattr(mod, "DURATION_S"))
            if err:
                blocks.append(f"level1: DURATION_S: {err}")
            elif meta_duration_s is not None and dur is not None:
                # exact match for declared window
                if abs(dur - meta_duration_s) > 1e-9:
                    blocks.append(
                        f"level1: DURATION_S in script ({dur}) does not match "
                        f"probe.json duration_s ({meta_duration_s})"
                    )
    else:
        # run: DURATION_S should not claim a watch window
        if hasattr(mod, "DURATION_S"):
            blocks.append(
                "level1: kind=run must not define DURATION_S "
                "(duration is for watch/snapshot only)"
            )

    return blocks
