"""The casebook's verbs: search by the problem, show one case, add a case or an instance of one, list, validate."""

from __future__ import annotations

import re
import time
from typing import Any

from casebook import cases, store
from casebook.bm25_ngram import Field, search as rank

# A stuck worker searches with what it sees: an error, a red reading, a behaviour it did not expect. The problem is
# written the same way, so it counts most; the diagnosis is how a worker who already suspects the cause words it;
# the instances' symptoms carry the raw text of past episodes, which is what an error message matches best.
SEARCH_FIELDS = (Field("problem", 3.0), Field("diagnosis", 1.5), Field("touches", 1.0), Field("symptoms", 1.0))
DEFAULT_LIMIT = 5
# Two diagnoses sharing this much of their content words are probably one diagnosis: the writer is asked to choose
# between joining the existing case and filing a new one, rather than the store deciding for it.
SAME_DIAGNOSIS = 0.4
_STOP = set("a an the of to in on at for and or but is are was were be been it its this that with as by from not no "
            "into than then so when while if which what who their there they them only one each any all".split())


def _read(case: dict[str, Any], name: str) -> Any:
    if name == "symptoms":
        return " ".join(str(i.get("symptom") or "") for i in case.get("instances") or [] if isinstance(i, dict))
    if name == "touches":
        return " ".join(case.get("touches") or [])
    return case.get(name) or ""


def summary(case: dict[str, Any], score: float | None = None) -> dict[str, Any]:
    instances = [i for i in case.get("instances") or [] if isinstance(i, dict)]
    out: dict[str, Any] = dict(id=case.get("id"), problem=case.get("problem"), diagnosis=case.get("diagnosis"),
                               fix=case.get("fix"), happened=len(instances))
    if case.get("touches"):
        out["touches"] = case["touches"]
    if instances:
        # The latest episode is the worked example: concrete where the case is general.
        last = instances[-1]
        out["example"] = {k: last[k] for k in ("where", "symptom", "change") if last.get(k)}
    if score is not None:
        out["score"] = score
    return out


def search(query: str, limit: int | None = None) -> dict[str, Any]:
    catalog = store.load_all()
    if not query.strip():
        return dict(ok=True, query="", total=len(catalog),
                    cases=[dict(id=k, problem=v.get("problem")) for k, v in sorted(catalog.items())])
    hits = rank(catalog, query, SEARCH_FIELDS, read=_read, limit=limit or DEFAULT_LIMIT,
                require_word_hit=True, min_ratio=0.25)
    return dict(ok=True, query=query, total=len(catalog), hits=[summary(catalog[h.item_id], h.score) for h in hits])


def show(case_id: str) -> dict[str, Any]:
    return dict(ok=True, case=store.load(case_id))


def list_cases() -> dict[str, Any]:
    catalog = store.load_all()
    return dict(ok=True, total=len(catalog), cases=[dict(id=k, problem=v.get("problem"),
                happened=len(v.get("instances") or [])) for k, v in sorted(catalog.items())])


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").casefold()) if w not in _STOP and len(w) > 2}


def similar(diagnosis: str, catalog: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Cases whose diagnosis shares most of its content words with this one."""
    catalog = store.load_all() if catalog is None else catalog
    mine = _words(diagnosis)
    out = []
    for case_id, case in catalog.items():
        theirs = _words(str(case.get("diagnosis") or ""))
        if not mine or not theirs:
            continue
        overlap = len(mine & theirs) / len(mine | theirs)
        if overlap >= SAME_DIAGNOSIS:
            out.append((overlap, case_id, case))
    out.sort(key=lambda item: -item[0])
    return [dict(id=cid, problem=c.get("problem"), diagnosis=c.get("diagnosis"), overlap=round(o, 2)) for o, cid, c in out[:3]]


def add(problem: str, diagnosis: str, fix: str, item: dict[str, Any], *, touches: list[str] | None = None,
        into: str | None = None, new: bool = False) -> dict[str, Any]:
    """File a case, or one more instance of an existing case (`into`). A diagnosis that looks like one already filed
    is sent back with the look-alikes unless `new` says it is a different one."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if into:
        case = store.load(into)
        case.setdefault("instances", []).append(item)
        for t in touches or []:
            if t not in case.setdefault("touches", []):
                case["touches"].append(t)
        case["updated_at"] = now
        refused = cases.problems(case)
        if refused:
            return dict(ok=False, refused=refused)
        store.save(case)
        return dict(ok=True, id=into, joined=True, happened=len(case["instances"]))
    catalog = store.load_all()
    case = dict(id=cases.slug(problem, set(catalog)), problem=problem.strip(), diagnosis=diagnosis.strip(),
                fix=fix.strip(), touches=list(touches or []), instances=[item], created_at=now, updated_at=now)
    refused = cases.problems(case)
    if refused:
        return dict(ok=False, refused=refused)
    if not new:
        alike = similar(diagnosis, catalog)
        if alike:
            return dict(ok=False, similar=alike,
                        hint="if one of these is the same diagnosis, add this episode to it with --into <id>; "
                             "if yours is a different one, file it with --new")
    store.save(case)
    return dict(ok=True, id=case["id"], created=True)


def validate(case_id: str | None = None) -> dict[str, Any]:
    catalog = store.load_all() if case_id is None else {case_id: store.load(case_id)}
    bad = {cid: p for cid, c in catalog.items() if (p := cases.problems(c))}
    return dict(ok=not bad, checked=len(catalog), refused=bad)
