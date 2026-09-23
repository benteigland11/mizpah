"""What a case is, and what makes one fit for the store.

A case says a problem the way it presented (the symptom a stuck worker sees), the diagnosis (what it turned out
to be), and the fix (what got it unstuck). Those three are written for the next worker, so they stay general: the
file names, task ids and numbers of the time it happened belong in an instance, which keeps the concrete episode
as the worked example. A case with no instance never happened, and is refused."""

from __future__ import annotations

import re
import time
from typing import Any

GENERAL_FIELDS = ("problem", "diagnosis", "fix")
# Bounds on the general fields: a case is read by a worker in the middle of its work, beside everything else it is
# holding. A problem that takes a paragraph to say is two problems, or an instance written into the problem.
LIMITS = dict(problem=400, diagnosis=700, fix=900)
INSTANCE_FIELDS = ("where", "symptom", "change", "cost", "source", "at")
SYMPTOM_LIMIT = 1500

# What an instance leaks into a general field: a path, a file with its extension, an id made of three or more
# snake_case parts (known, task and probe ids look like that; words of the domain rarely do).
_PATH = re.compile(r"(?<![\w.])(?:\.{0,2}/)?(?:[\w.-]+/)+[\w.-]+")
_FILE = re.compile(r"\b[\w-]+\.(?:py|mid|midi|json|md|pdf|svg|ly|wav|mp3|csv|txt|png|jpg|yaml|yml|toml|html|js|ts|dart|sh)\b", re.I)
_ID = re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+){2,}\b")


def slug(text: str, taken: set[str] | None = None) -> str:
    words = re.findall(r"[a-z0-9]+", text.casefold())
    base = "-".join(words[:8]).strip("-") or "case"
    base = base[:72].rstrip("-")
    taken = taken or set()
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def specifics(text: str) -> list[str]:
    """The pieces of one episode a general field names: paths, files, ids."""
    found: list[str] = []
    for pattern in (_PATH, _FILE, _ID):
        for match in pattern.finditer(text or ""):
            value = match.group(0)
            if value not in found and not value.startswith(("http://", "https://")):
                found.append(value)
    return found


def problems(case: dict[str, Any]) -> list[str]:
    """Why a case is not fit for the store; empty when it is."""
    out: list[str] = []
    if not isinstance(case, dict):
        return ["a case is a JSON object"]
    for name in GENERAL_FIELDS:
        value = case.get(name)
        if not isinstance(value, str) or not value.strip():
            out.append(f"{name}: missing")
            continue
        if len(value) > LIMITS[name]:
            out.append(f"{name}: {len(value)} characters over {LIMITS[name]}; say the one thing, and put the episode in an instance")
        # The fix may name the tool or command that did it; the problem and diagnosis are what the next worker
        # recognises its own situation by, and a name from this episode makes them about this episode only.
        if name != "fix":
            leaked = specifics(value)
            if leaked:
                out.append(f"{name}: names {', '.join(leaked[:4])} from one episode; say what kind of thing it is, "
                           "and keep the name in the instance")
    instances = case.get("instances")
    if not isinstance(instances, list) or not instances:
        out.append("instances: a case needs the episode it came from (where it happened and the symptom as it showed)")
    else:
        for n, item in enumerate(instances, 1):
            if not isinstance(item, dict):
                out.append(f"instance {n}: not an object")
                continue
            for name in ("where", "symptom"):
                if not str(item.get(name) or "").strip():
                    out.append(f"instance {n}: {name} missing")
    touches = case.get("touches", [])
    if not isinstance(touches, list) or not all(isinstance(t, str) for t in touches):
        out.append("touches: a list of the tools, formats or widgets the problem involves")
    return out


def instance(where: str, symptom: str, change: str = "", cost: int | None = None, source: str = "") -> dict[str, Any]:
    item: dict[str, Any] = dict(where=where.strip(), symptom=symptom.strip()[:SYMPTOM_LIMIT])
    if change.strip():
        item["change"] = change.strip()
    if cost is not None:
        item["cost"] = int(cost)
    if source.strip():
        item["source"] = source.strip()
    item["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return item
