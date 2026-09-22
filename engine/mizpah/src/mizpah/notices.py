"""What the Deputy files up to the Administrator's inbox.

One line per notice in `<deputy root>/notices.jsonl`, in the app's document shape; the app reads the file into
the tray as NOTICE FROM THE DEPUTY, beside the project paper and the Board's. Plumbing only: the host files one
where a condition holds, never on the seat's word. Nothing calls `send` yet.

A notice has a stable `number`. Sending the same number again is a revision: the app keeps the latest, and a
notice the Administrator dismissed stays dismissed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

FROM = 'The Deputy'
KIND = 'deputyNotice'
FILE = 'notices.jsonl'


def line(text: str, *, lead: str = '', mono: bool = False, emphasis: bool = False, link: str = '') -> dict[str, Any]:
    """One line of a section, as the sheet draws it. `link` is `<kind>:<number>` or `brief:<project id>`."""
    out: dict[str, Any] = dict(text=text)
    if lead:
        out['lead'] = lead
    if mono:
        out['mono'] = True
    if emphasis:
        out['emphasis'] = True
    if link:
        out['link'] = link
    return out


def send(root: Path, number: str, title: str, sections: list[tuple[str, list[dict[str, Any]]]], *,
         status: str = 'FYI', hot: bool = False, header: list[tuple[str, str]] = ()) -> dict[str, Any]:
    """File one notice. `title` is the one line the tray shows; `sections` are (heading, lines)."""
    doc = dict(kind=KIND, number=number, title=title, at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               **{'from': FROM}, status=status, header=[dict(k=k, v=v) for k, v in header],
               sections=[dict(heading=h, lines=ls) for h, ls in sections])
    if hot:
        doc['hot'] = True
    root.mkdir(parents=True, exist_ok=True)
    with (root/FILE).open('a') as handle:
        handle.write(json.dumps(doc)+'\n')
    return doc


def read(root: Path) -> list[dict[str, Any]]:
    """Every notice on file, latest per number, in the order first sent."""
    path = root/FILE
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    for raw in [ln for ln in path.read_text(errors='replace').split('\n') if ln.strip()]:
        if not raw.strip():
            continue
        try:
            doc = json.loads(raw)
        except ValueError:
            continue
        latest[doc['number']] = doc
    return list(latest.values())
