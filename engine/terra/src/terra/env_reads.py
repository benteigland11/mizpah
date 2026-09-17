"""Record which environment variables a probe actually read.

The provenance hole this closes: a probe's DECLARED inputs
(``input_bindings`` / ``inputs``) only cover ``known:``/``assumption:``
bindings. A probe that reaches into ``os.environ`` for a value — the usual
shape of a hand-pinned override while a real known is stale — consumes an
input that the run record does not mention at all. A forced run and a bare
run are then byte-indistinguishable in the ledger, and the forced one can
graduate a belief. That happened on 2026-07-27 (a `combat_kg` pin while
`combat_weight_kg` was stale); it was caught by a lead reading the probe
source, which is not a control that scales.

This does not stop a probe reading the environment — that is legitimate
(paths, tokens, tuning). It makes the read VISIBLE, so `run show` can answer
"what did this measurement actually depend on".

Deliberately narrow: we record reads performed by probe code in THIS process
during the run. Subprocesses inherit the real environment and are not
instrumented — a probe that shells out is outside this instrument's reach,
and the record says so via ``complete: false`` rather than implying coverage
it does not have.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Any, Iterator

# Values for these are never recorded — only the fact of the read.
_SECRET_RE = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|SESSION)",
    re.IGNORECASE,
)
# Ambient noise every Python process touches; recording it would bury signal.
_BORING = frozenset(
    {
        "PATH", "HOME", "PWD", "OLDPWD", "SHELL", "TERM", "LANG", "LC_ALL",
        "LC_CTYPE", "USER", "USERNAME", "LOGNAME", "TMPDIR", "TEMP", "TMP",
        "PYTHONPATH", "PYTHONHASHSEED", "PYTHONDONTWRITEBYTECODE",
        "PYTHONIOENCODING", "PYTHONUNBUFFERED", "VIRTUAL_ENV", "CONDA_PREFIX",
        "HOSTNAME", "SHLVL", "_",
    }
)

_MAX_VALUE_CHARS = 200


def _redact(key: str, value: str | None) -> Any:
    if value is None:
        return None
    if _SECRET_RE.search(key):
        return "<redacted>"
    if len(value) > _MAX_VALUE_CHARS:
        return value[:_MAX_VALUE_CHARS] + "…"
    return value


class _RecordingEnviron(dict):
    """dict subclass that logs key reads.

    ``os.environ`` is an ``os._Environ`` mapping; putting a plain dict in its
    place for the duration of the call keeps ``os.environ["X"]`` /
    ``.get`` / ``in`` working for probe code while letting us observe the
    keys. Writes pass through to the real mapping so a probe that sets an
    env var for a subprocess still behaves correctly.
    """

    def __init__(self, source: Any, sink: dict[str, Any]) -> None:
        super().__init__(source)
        self._sink = sink
        self._real = source

    def _note(self, key: Any) -> None:
        # Keys are NOT guaranteed str. `os.environ.get(b'PATH')` is legal and
        # os.supports_bytes_environ paths do it, so a bytes key slipped past a
        # str-keyed filter and later exploded `sorted()` comparing str<bytes.
        # Normalize FIRST so filtering and storage are both type-stable.
        if isinstance(key, bytes):
            try:
                key = key.decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                return
        elif not isinstance(key, str):
            return
        if key in _BORING:
            return
        if key not in self._sink:
            self._sink[key] = _redact(key, super().get(key))

    def __getitem__(self, key: str) -> str:
        self._note(key)
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        self._note(key)
        return super().get(key, default)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            self._note(key)
        return super().__contains__(key)

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(key, value)
        self._real[key] = value

    def setdefault(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        self._note(key)
        return super().setdefault(key, default)


@contextmanager
def record_env_reads(sink: dict[str, Any]) -> Iterator[None]:
    """Swap os.environ for a recording proxy for the duration of the block.

    Restores unconditionally. Single-process CLI: probe runs are sequential,
    so a process-global swap is safe here and nowhere else.
    """
    real = os.environ
    proxy = _RecordingEnviron(real, sink)
    os.environ = proxy  # type: ignore[assignment]
    try:
        yield
    finally:
        os.environ = real  # type: ignore[assignment]


def summarize(sink: dict[str, Any]) -> dict[str, Any]:
    """Run-record block. ``complete`` is honest about subprocess blindness.

    MUST NOT RAISE. This runs AFTER ``run()`` returns, so an exception here
    destroys a completed measurement: the full compute is paid, no run is
    stamped, and `terra run list` shows nothing — indistinguishable from
    "never ran". That happened for real on 2026-07-28 and cost a 12-flight
    sim run. Provenance is never worth a measurement.
    """
    try:
        read = {str(k): v for k, v in sorted(sink.items(), key=lambda kv: str(kv[0]))}
    except Exception as e:  # noqa: BLE001
        return {
            "read": {},
            "count": len(sink),
            "complete": False,
            "note": f"env-read summary degraded: {type(e).__name__}: {e}",
        }
    return {
        "read": read,
        "count": len(sink),
        "complete": False,
        "note": (
            "in-process reads by probe code only; subprocesses are not "
            "instrumented, and ambient shell vars are filtered"
        ),
    }
