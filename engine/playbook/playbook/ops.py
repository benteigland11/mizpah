"""File-backed operations over playbook procedure documents in the global store."""

from __future__ import annotations

import re

import os
from pathlib import Path
from typing import Any, Mapping

from playbook import library, store
from playbook.bm25_ngram import Field, search as rank_search
from playbook.widget_api import widget

_SEARCH_FIELDS = (
    Field("id", 4.0),
    Field("title", 4.0),
    Field("tags", 3.0),
    Field("description", 2.5),
    Field("titles", 2.0),
)
_STOPWORDS = (
    "a",
    "an",
    "the",
    "to",
    "of",
    "and",
    "or",
    "for",
    "in",
    "on",
    "is",
    "it",
    "this",
    "that",
    "i",
    "we",
    "you",
    "my",
    "our",
    "want",
    "need",
    "please",
    "how",
    "do",
    "does",
    "into",
    "as",
    "with",
    "from",
)
DEFAULT_SEARCH_LIMIT = 8
MAX_SEARCH_LIMIT = 50
WEAK_SEARCH_LIMIT = 3


def search_procedures(query: str, limit: int | None = None, include_retired: bool = False,
                      target_dir: str | Path = ".") -> dict[str, Any]:
    """Search the global store. Empty query lists procedures (summaries only).

    Local first: walks already open under the working tree and not finished come back ahead of the
    hits, as `open_here`, each with its next step — a worker whose window rolled over searches again
    from nothing, and what it had already started is the first thing it should see.
    Retired procedures (use it or lose it: see playbook.library) are left out unless asked
    for; every procedure that appears in the result is touched as searched."""
    catalog = _load_catalog()
    if not include_retired:
        retired = library.retired_ids()
        catalog = {k: v for k, v in catalog.items() if k not in retired}
    result = _search(catalog, query, limit)
    library.touch([h["id"] for h in result.get("hits", [])], "search")
    here = open_walks(None, target_dir)
    if here:
        result = {"ok": result["ok"], "open_here": here,
                  "note": "these walks are already open in this workspace and not finished: continue them "
                          "before opening anything from the hits",
                  **{k: v for k, v in result.items() if k != "ok"}}
    return result


def _search(catalog: dict[str, dict[str, Any]], query: str, limit: int | None) -> dict[str, Any]:
    capped = _clamp_limit(limit)
    if not query.strip():
        ordered = sorted(catalog)
        hits = [_hit_payload(item_id, catalog[item_id], score=None, snippet=None) for item_id in ordered[:capped]]
        return {"ok": True, "query": "", "hits": hits, "limit": capped, "total": len(ordered)}
    ranked = rank_search(
        catalog,
        query,
        _SEARCH_FIELDS,
        ngram=3,
        ngram_weight=0.4,
        stopwords=_STOPWORDS,
        require_word_hit=True,
        min_ratio=0.5,
    )
    if ranked:
        hits = [_hit_payload(hit.item_id, catalog[hit.item_id], score=hit.score, snippet=None) for hit in ranked[:capped]]
        return {"ok": True, "query": query, "hits": hits, "limit": capped, "total": len(ranked)}
    return _weak_search(catalog, query, capped)


def _weak_search(catalog: dict[str, dict[str, Any]], query: str, capped: int) -> dict[str, Any]:
    """Strict search found nothing. Retry without the word-hit and ratio floors.

    A natural-language ask often shares no literal word with a procedure that
    still covers it. Returning nothing reads as "no such procedure" and the
    caller improvises, so offer the nearest candidates and label them weak.

    The tail is still trimmed by ratio. There is deliberately no absolute score
    floor: BM25 scores move with corpus size and field weights, so any constant
    would quietly start dropping good matches as the store grows. A query that
    matches nothing real can still surface a low-scoring procedure here, which
    is why the payload says weak and the note tells the caller to check the
    description before trusting it.
    """
    ranked = rank_search(
        catalog,
        query,
        _SEARCH_FIELDS,
        ngram=3,
        ngram_weight=0.4,
        stopwords=_STOPWORDS,
        require_word_hit=False,
        min_ratio=0.5,
    )
    shown = min(capped, WEAK_SEARCH_LIMIT)
    hits = [_hit_payload(hit.item_id, catalog[hit.item_id], score=hit.score, snippet=None) for hit in ranked[:shown]]
    payload: dict[str, Any] = {
        "ok": True,
        "query": query,
        "hits": hits,
        "limit": capped,
        "total": len(ranked),
        "weak": True,
    }
    if hits:
        payload["note"] = (
            "No strong match. These are the nearest procedures by loose similarity — "
            "read the descriptions and only use one if it genuinely covers the job."
        )
    else:
        payload["note"] = "No procedure resembles this query. The store may not cover it yet."
    return payload


def _clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_SEARCH_LIMIT
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be an integer >= 1")
    return min(limit, MAX_SEARCH_LIMIT)


def _hit_payload(
    item_id: str,
    record: dict[str, Any],
    score: float | None,
    snippet: dict[str, str] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": item_id,
        "title": record.get("title") or item_id,
        "description": record.get("description", ""),
    }
    if score is not None:
        payload["score"] = score
    if snippet is not None:
        payload["snippet"] = snippet
    # The titles are what `start --title` takes; a hit that hides them costs the
    # caller a `load` (or several guesses) before it can start.
    titles = record.get("titles")
    if isinstance(titles, list) and titles:
        payload["steps"] = list(titles)
        payload["start"] = f"playbook start {item_id} --title {titles[0]!r}"
    return payload


def _load_catalog() -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    root = store.procedures_dir()
    if not root.is_dir():
        return catalog
    for path in sorted(root.glob("*.json")):
        if path.name.startswith("."):
            continue
        try:
            document = read_document(path)
        except (OSError, ValueError):
            continue
        procedure_id = document.get("id")
        if not isinstance(procedure_id, str) or not procedure_id.strip():
            procedure_id = path.stem
        steps = document.get("steps") if isinstance(document.get("steps"), list) else []
        titles = [
            step.get("title")
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("title"), str)
        ]
        title = document.get("title")
        if not isinstance(title, str) or not title.strip():
            title = procedure_id
        catalog[procedure_id] = {
            "id": procedure_id,
            "title": title.strip(),
            "description": document.get("description", ""),
            "tags": document.get("tags") if isinstance(document.get("tags"), list) else [],
            "titles": titles,
        }
    return catalog


def create_procedure(
    procedure_id: str,
    title: str,
    description: str,
    tags: list[str],
    steps: list[Mapping[str, Any]] | None = None,
) -> Path:
    """Write a new procedure. Pass steps to author the whole thing in one call."""
    target = store.procedure_path(procedure_id)
    if target.exists():
        raise FileExistsError(f"already exists: {target}")
    document = widget.new_procedure(procedure_id, title, description, tags)
    if steps:
        document = _append_steps(document, steps)
    write_document(target, document)
    library.touch([procedure_id], "edit")
    return target


def _append_steps(
    document: Mapping[str, Any],
    steps: list[Mapping[str, Any]],
    after: str | None = None,
) -> dict[str, Any]:
    """Insert steps in order, in memory. Each entry needs a title and a do."""
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty array")
    updated = dict(document)
    cursor = after
    for position, entry in enumerate(steps):
        if not isinstance(entry, Mapping):
            raise ValueError(f"steps[{position}] must be an object with title and do")
        title = entry.get("title")
        do = entry.get("do")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"steps[{position}].title must be a non-empty string")
        if not isinstance(do, str) or not do.strip():
            raise ValueError(f"steps[{position}].do must be a non-empty string")
        linked = entry.get("procedure")
        if linked is not None:
            _require_procedure(str(linked), owner=str(updated.get("id") or ""))
        updated = widget.add_step(updated, title, do, after=cursor, procedure=str(linked) if linked else None)
        cursor = title.strip()
    return updated


def _require_procedure(procedure_id: str, owner: str = "") -> None:
    """A linked step must point at a procedure that exists and is another one: a dangling link is a step nobody
    can walk, and a self-link is a loop (a step that says "run this procedure" from inside it — drone-pack-sizing-probe
    linked six of its own steps to itself, 2026-09-19). "See step N" is prose in `do`, not a link."""
    if owner and procedure_id == owner:
        raise ValueError(f"a step may not link to its own procedure {procedure_id!r}; a link runs another procedure "
                         "as this step — refer to another step of this one in the step's text instead")
    if not store.procedure_path(procedure_id).is_file():
        raise ValueError(f"linked procedure {procedure_id!r} does not exist; search for its id or create it first")


def load_procedure(procedure_id: str, full: bool = True) -> dict[str, Any]:
    """Return the whole procedure in one call. full=False gives step titles only."""
    document = read_document(store.procedure_path(procedure_id))
    _require_valid(document, procedure_id)
    library.touch([procedure_id], "use")
    steps = document.get("steps") if isinstance(document.get("steps"), list) else []
    listing = []
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("title"), str):
            continue
        entry: dict[str, Any] = {"title": step["title"]}
        if full:
            entry["do"] = step.get("do")
        listing.append(entry)
    return {
        "ok": True,
        "id": document.get("id"),
        "title": document.get("title"),
        "description": document.get("description"),
        "full": full,
        "steps": listing,
    }


def start_procedure(procedure_id: str, title: str | None = None, step: int | None = None) -> dict[str, Any]:
    """Re-read one step by unique title: its do, its position, and the neighbouring titles.

    Without a title, start at the first step: the caller has just found the procedure
    and cannot know its step titles yet.
    """
    document = read_document(store.procedure_path(procedure_id))
    _require_valid(document, procedure_id)
    library.touch([procedure_id], "use")
    steps = document.get("steps") if isinstance(document.get("steps"), list) else []
    if step is not None:
        if not isinstance(step, int) or not 1 <= step <= len(steps) or not isinstance(steps[step-1], dict):
            raise ValueError(f"procedure {procedure_id!r} has steps 1..{len(steps)}; no step {step}")
        title = steps[step-1].get("title")
    if title is None or not str(title).strip():
        if not steps or not isinstance(steps[0], dict) or not isinstance(steps[0].get("title"), str):
            raise ValueError(f"procedure {procedure_id!r} has no steps to start at")
        title = steps[0]["title"]
    marker = str(title).strip()
    index = next(
        (i for i, step in enumerate(steps) if isinstance(step, dict) and step.get("title") == marker),
        None,
    )
    if index is None:
        titles = [step.get("title") for step in steps if isinstance(step, dict)]
        raise ValueError(f"no step titled {marker!r}; steps are: {titles!r}")
    current = steps[index]
    previous = steps[index - 1] if index > 0 else None
    following = steps[index + 1] if index + 1 < len(steps) else None
    return {
        "ok": True,
        "id": document.get("id"),
        "title": document.get("title"),
        "at": current.get("title"),
        "position": f"{index + 1}/{len(steps)}",
        "do": current.get("do"),
        "prev": previous.get("title") if isinstance(previous, dict) else None,
        "next": following.get("title") if isinstance(following, dict) else None,
        # The exact command for the next step, to copy rather than recall.
        "then": (f"playbook start {procedure_id} --step {index + 2}" if isinstance(following, dict)
                 else "(last step)"),
    }


def _require_valid(document: Mapping[str, Any], procedure_id: str) -> None:
    """Raise with every schema error. Reads carry their own validation, so
    callers never need a separate validate step to find out what is wrong."""
    result = widget.validate_procedure(document)
    if result.valid:
        return
    detail = "; ".join(f"{item.path}: {item.message}" for item in result.errors)
    raise ValueError(f"procedure {procedure_id!r} is invalid: {detail}")


def validate_procedure(procedure_id: str) -> dict[str, Any]:
    document = read_document(store.procedure_path(procedure_id))
    result = widget.validate_procedure(document)
    steps = document.get("steps")
    step_summaries = []
    dangling = []
    self_links = []
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                step_summaries.append({"id": step.get("id"), "title": step.get("title"),
                                       **({"procedure": step["procedure"]} if step.get("procedure") else {})})
                if step.get("procedure") and str(step["procedure"]) == str(document.get("id")):
                    self_links.append(str(step.get("title")))
                elif step.get("procedure") and not store.procedure_path(str(step["procedure"])).is_file():
                    dangling.append(str(step["procedure"]))
    return {
        "valid": result.valid and not dangling and not self_links,
        "errors": [{"path": err.path, "message": err.message} for err in result.errors]
        + [{"path": "$.steps", "message": f"linked procedure {d!r} does not exist"} for d in dangling]
        + [{"path": "$.steps", "message": f"step {t!r} links to its own procedure; a link runs another procedure"} for t in self_links],
        "id": document.get("id"),
        "description": document.get("description"),
        "tags": document.get("tags", []),
        "steps": step_summaries,
    }


def edit_meta(
    procedure_id: str,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    target = store.procedure_path(procedure_id)
    document = read_document(target)
    updated = widget.edit_procedure(document, title=title, description=description, tags=tags)
    write_document(target, updated)
    library.touch([procedure_id], "edit")
    return {
        "ok": True,
        "id": updated["id"],
        "title": updated["title"],
        "description": updated["description"],
        "tags": updated["tags"],
    }


def add_step(procedure_id: str, title: str, do: str, after: str | None = None,
             procedure: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"title": title, "do": do}
    if procedure:
        entry["procedure"] = procedure
    return add_steps(procedure_id, [entry], after=after)


def add_steps(
    procedure_id: str,
    steps: list[Mapping[str, Any]],
    after: str | None = None,
) -> dict[str, Any]:
    """Append or insert several steps in one read/write. Order is preserved."""
    target = store.procedure_path(procedure_id)
    document = read_document(target)
    updated = _append_steps(document, steps, after=after)
    write_document(target, updated)
    library.touch([procedure_id], "edit")
    added = [str(entry["title"]).strip() for entry in steps]
    titles = [item.get("title") for item in updated["steps"]]
    return {"ok": True, "id": updated["id"], "added": added, "steps": titles}


def edit_step(
    procedure_id: str,
    title: str,
    new_title: str | None = None,
    do: str | None = None,
    procedure: str | None = None,
) -> dict[str, Any]:
    target = store.procedure_path(procedure_id)
    document = read_document(target)
    if procedure:
        _require_procedure(procedure, owner=str(document.get("id") or procedure_id))
    updated = widget.edit_step(document, title, new_title=new_title, do=do, procedure=procedure)
    write_document(target, updated)
    library.touch([procedure_id], "edit")
    marker = (new_title or title).strip()
    step = next(item for item in updated["steps"] if item.get("title") == marker)
    # The step as it now reads, so the caller sees the edit landed: a worker that got only the title back
    # re-edited the same step thirteen times answering a correction it could not see it had met.
    return {"ok": True, "id": updated["id"], "step_id": step["id"], "title": step["title"], "do": step.get("do", ""),
            **({"procedure": step["procedure"]} if step.get("procedure") else {})}


def remove_step(procedure_id: str, title: str) -> dict[str, Any]:
    target = store.procedure_path(procedure_id)
    document = read_document(target)
    updated = widget.remove_step(document, title)
    write_document(target, updated)
    library.touch([procedure_id], "edit")
    titles = [step.get("title") for step in updated["steps"]]
    return {"ok": True, "id": updated["id"], "removed": title, "steps": titles}


def move_step(procedure_id: str, title: str, position: int) -> dict[str, Any]:
    """Move a step (by its unique title) to a 1-based position; the others keep their order."""
    target = store.procedure_path(procedure_id)
    document = read_document(target)
    steps = document.get("steps")
    if not isinstance(steps, list):
        raise ValueError("document steps must be an array")
    wanted = title.strip()
    indices = [i for i, step in enumerate(steps) if isinstance(step, Mapping) and step.get("title") == wanted]
    if len(indices) != 1:
        raise ValueError(f"step title must match exactly one step, matched {len(indices)}: {wanted!r}")
    if not 1 <= position <= len(steps):
        raise ValueError(f"position must be 1..{len(steps)}, got {position}")
    step = steps.pop(indices[0])
    steps.insert(position - 1, step)
    result = widget.validate_procedure(document)
    if not result.valid:
        raise ValueError("; ".join(str(e) for e in result.errors))
    write_document(target, document)
    library.touch([procedure_id], "edit")
    titles = [s.get("title") for s in steps]
    return {"ok": True, "id": document["id"], "moved": wanted, "position": position, "steps": titles}


def delete_procedure(procedure_id: str) -> dict[str, Any]:
    """Remove a procedure from the store. Refused while another procedure's step still links it:
    the link would dangle, and the next worker following that step would find nothing."""
    target = store.procedure_path(procedure_id)
    if not target.exists():
        raise ValueError(f"no procedure {procedure_id!r}")
    linkers: list[str] = []
    for path in sorted(store.procedures_dir().glob("*.json")):
        if path == target:
            continue
        try:
            other = read_document(path)
        except Exception:  # noqa: BLE001 — a bad neighbour is not this one's problem
            continue
        if any(isinstance(step, Mapping) and step.get("procedure") == procedure_id for step in other.get("steps") or []):
            linkers.append(str(other.get("id") or path.stem))
    if linkers:
        raise ValueError(f"{procedure_id!r} is linked by a step of: {', '.join(linkers)} — unlink those steps first")
    target.unlink()
    library.forget(procedure_id)
    return {"ok": True, "deleted": procedure_id}


def read_document(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    return widget.parse_procedure(text)


def write_document(path: str | Path, document: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = widget.dump_procedure(document)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, target)


OPEN_DIR = ".playbook/open"


_STEP_LINE = re.compile(r"^- \[( |x|-)\] \*\*(\d+)\. (.*?)\*\*\s*$")


def walk_steps(path: Path) -> list[tuple[str, int, str]]:
    """(mark, number, title) for every step line of an open walk."""
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _STEP_LINE.match(line)
        if m:
            found.append((m.group(1), int(m.group(2)), m.group(3)))
    return found


def open_walks(procedure_id: str | None, target_dir: str | Path = ".") -> list[dict[str, Any]]:
    """Walks under the working tree that still have unticked steps, oldest first — of one procedure,
    or of every procedure when `procedure_id` is None."""
    out_dir = Path(target_dir) / OPEN_DIR
    if not out_dir.is_dir():
        return []
    result = []
    pattern = f"{procedure_id}--*.md" if procedure_id else "*--*.md"
    for path in sorted(out_dir.glob(pattern), key=lambda p: p.stat().st_mtime):
        steps = walk_steps(path)
        left = [(n, t) for mark, n, t in steps if mark == " "]
        if left:
            result.append(dict(id=path.name.split("--", 1)[0], path=str(path), steps=len(steps), unticked=len(left),
                               next=f"{left[0][0]}. {left[0][1]}"))
    return result


def _walk_file(target: str, target_dir: str | Path) -> Path:
    path = Path(target)
    if path.is_file():
        return path
    walks = open_walks(target, target_dir)
    if not walks:
        raise ValueError(f"no open walk of '{target}' with unticked steps under {Path(target_dir) / OPEN_DIR}")
    if len(walks) > 1:
        raise ValueError(f"'{target}' has {len(walks)} unfinished walks; name the file: " + ", ".join(w["path"] for w in walks))
    return Path(walks[0]["path"])


_LINK_LINE = re.compile(r"^\s*→ this step is another procedure: `playbook open ([A-Za-z0-9._-]+) --for")


def _linked_steps(lines: list[str]) -> dict[int, str]:
    """step number -> the procedure id that step links, from a walk file's own text."""
    out: dict[int, str] = {}
    current = None
    for line in lines:
        m = _STEP_LINE.match(line)
        if m:
            current = int(m.group(2)); continue
        k = _LINK_LINE.match(line)
        if k and current is not None:
            out[current] = k.group(1)
    return out


def _walk_closed(procedure_id: str, target_dir: str | Path) -> bool:
    """A walk of the procedure exists under the working tree with no unticked box."""
    out_dir = Path(target_dir) / OPEN_DIR
    if not out_dir.is_dir():
        return False
    for path in out_dir.glob(f"{procedure_id}--*.md"):
        steps = walk_steps(path)
        if steps and all(mark != " " for mark, _, _ in steps):
            return True
    return False


def tick_walk(target: str, done: list[int] | None = None, not_needed: list[int] | None = None,
              target_dir: str | Path = ".", because: str = "") -> dict[str, Any]:
    """Mark steps of an open walk in one call: `done` get `[x]`, `not_needed` get `[-]`. A worker ticking by
    hand spent thirteen turns of grep, read and edit on seven boxes.

    A step is skipped one at a time, with its reason written under it: the procedure is the validation of the
    work, and each step is its own decision. There is no verb that skips a walk whole — three procedures were
    closed with one reason each ("already covered by the probe") on a piece whose every pedal hold crossed a
    harmony, which the skipped procedure's fourth step checks."""
    done = list(done or []); not_needed = list(not_needed or [])
    if not done and not not_needed:
        raise ValueError("tick needs --done and/or --skip with step numbers, e.g. --done 1,3 --skip 2 --because ...")
    if len(not_needed) > 1:
        raise ValueError("skip one step per call, each with its own --because: a step is its own decision")
    because = str(because or "").strip()
    if not_needed and not because:
        raise ValueError("--skip needs --because: why this step does not apply to what is in front of you (one line; it goes under the step)")
    if set(done) & set(not_needed):
        raise ValueError("a step is done or not needed, not both: " + ", ".join(str(n) for n in sorted(set(done) & set(not_needed))))
    path = _walk_file(target, target_dir)
    lines = path.read_text(encoding="utf-8").splitlines()
    # A step that is another procedure closes only once that procedure's walk is closed: opened, and every
    # box ticked or skipped with a reason. Ticking the parent box on the strength of "the widget did it" left
    # three linked procedures unwalked and unrecorded in one task (attempt 4, 2026-09-21); the worker has to try.
    linked = _linked_steps(lines)
    unwalked = [n for n in sorted(set(done) | set(not_needed)) if n in linked and not _walk_closed(linked[n], target_dir)]
    if unwalked:
        raise ValueError("step(s) " + ", ".join(str(n) for n in unwalked) + " are other procedures; open and finish each first "
                         "(every box [x], or [-] with its reason), then tick this box: "
                         + "; ".join(f"{n} -> `playbook open {linked[n]} --for ...`" for n in unwalked))
    seen: set[int] = set(); marked = []
    for i, line in enumerate(lines):
        m = _STEP_LINE.match(line)
        if not m:
            continue
        n = int(m.group(2)); seen.add(n)
        mark = "x" if n in done else "-" if n in not_needed else None
        if mark is not None:
            lines[i] = re.sub(r"^- \[( |x|-)\]", f"- [{mark}]", line, count=1)
            if mark == "-":
                lines[i] += f"\n\n  not needed here: {because}"
            marked.append(n)
    missing = sorted((set(done) | set(not_needed)) - seen)
    if missing:
        raise ValueError(f"no such step(s) {missing}; the walk has steps {sorted(seen)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    left = [n for mark, n, _ in walk_steps(path) if mark == " "]
    return {"ok": True, "path": str(path), "marked": marked, "unticked": left}


def open_procedure(procedure_id: str, purpose: str, target_dir: str | Path = ".", again: bool = False) -> dict[str, Any]:
    """Materialise a whole procedure as a checklist file in the working tree, to be read once and followed.

    `start --step` drips one step per call, which a small-window model needed; a capable model spends a
    turn per step for nothing. Every open is its own copy, named and headed by what it is for, so a
    procedure walked twice in one task (two artifacts, two sources) keeps two checklists — but only on
    `--again`: a walk still in progress is handed back with its next step, because a worker whose window
    rolled over opens the same procedure a second time and leaves a fresh unticked copy behind.
    """
    purpose = str(purpose or "").strip()
    if not purpose:
        raise ValueError("open needs --for: what this walk of the procedure is for (an unknown, an artifact, a source)")
    if not again:
        unfinished = open_walks(procedure_id, target_dir)
        if unfinished:
            walk = unfinished[-1]
            return {"ok": True, "id": procedure_id, "already_open": True, "path": walk["path"], "next": walk["next"],
                    "unticked": walk["unticked"],
                    "note": ("this procedure is already open here; continue from its next step, tick `[x]` done or `[-]` "
                             "not needed (one per call, with --because). `--again` opens a "
                             "second walk for a second thing.")}
    document = read_document(store.procedure_path(procedure_id))
    _require_valid(document, procedure_id)
    library.touch([procedure_id], "use")
    steps = [s for s in (document.get("steps") or []) if isinstance(s, dict)]
    lines = [f"# {document.get('title') or procedure_id}", "", f"procedure: `{procedure_id}`", f"for: {purpose}", ""]
    if document.get("description"):
        lines += [str(document["description"]).strip(), ""]
    if document.get("tags"):
        lines += ["tags: " + ", ".join(str(t) for t in document["tags"]), ""]
    lines += ["This procedure is the validation of your work: each step is a check or a change someone found necessary, "
              "written down so the next piece gets it too. Do the steps in order, against what is in front of you, and tick "
              "as you go — in the same command as the step's work: `playbook tick <this file> --done N` when a step is done "
              "(several when several closed together). A step that does not apply here is `--skip N --because \"...\"`, one "
              "per call, the reason written under it — \"already done\" and \"covered by the probe\" are not reasons: do the "
              "step and show what it found. There is no way to close a walk whole. The steps are knowledge earned on another "
              "task: adapt their specifics (names, keys, counts, the probe they mention) to what is in front of you, and do "
              "them. A step that is another procedure is walked as well — open it, finish it, then tick here; `tick` refuses "
              "a linked step whose walk is not closed. Improve the procedure afterwards with `playbook edit-step` / `add-step` "
              "where a step fell short.", ""]
    for i, step in enumerate(steps, 1):
        lines += [f"- [ ] **{i}. {step.get('title', '')}**", "", f"  {str(step.get('do', '')).strip()}", ""]
        if step.get("procedure"):
            linked = str(step["procedure"])
            lines += [f"  → this step is another procedure: `playbook open {linked} --for \"{purpose}: {step.get('title', '')}\"` "
                      f"— walk that checklist, then tick this box.", ""]
    out_dir = Path(target_dir) / OPEN_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", purpose.lower()).strip("-")[:48] or "walk"
    path = out_dir / f"{procedure_id}--{slug}.md"
    n = 2
    while path.exists():
        path = out_dir / f"{procedure_id}--{slug}-{n}.md"
        n += 1
    path.write_text("\n".join(lines))
    return {"ok": True, "id": procedure_id, "title": document.get("title"), "for": purpose, "steps": len(steps), "path": str(path)}
