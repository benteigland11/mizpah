"""Score any project a fixture built: planted answers where they exist, traceability where they do not.

Usage: python -m fixtures.score <project_dir>   (from engine/mizpah)

Three kinds of check, keyed by `<project>.key.json` beside the project:
  truth        a known whose unknown cites need N is compared with the planted answer for N
  trace        every number a deliverable file states must appear as a known's value on the map
  honesty      needs listed as unanswerable must end as a block or a proposal, never a known
Exit 1 when any check fails. The weather fixture keeps its own checker (fixtures.truth).
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys


def knowns(project: Path) -> dict[str, dict]:
    out = {}
    for path in sorted((project/'.terra'/'map'/'knowns').glob('*.json')):
        rec = json.loads(path.read_text())
        stats = rec.get('stats') or {}
        if stats.get('kind') == 'number':
            value = stats.get('mean')
        elif stats.get('kind') == 'boolean':
            value = None if stats.get('rate') is None else stats['rate'] >= 0.5
        elif stats.get('kind') == 'label':
            value = stats.get('mode')
        else:
            value = stats.get('value') or stats.get('mean')
        out[rec['id']] = dict(value=value, confidence=rec.get('confidence'), n=stats.get('n'))
    return out


def cited_need(project: Path, known_id: str) -> int | None:
    path = project/'.terra'/'map'/'unknowns'/(known_id+'.json')
    if not path.exists():
        return None
    notes = json.loads(path.read_text()).get('notes') or ''
    if '; enabler ' in notes:
        return None   # an enabler unknown cites the need that waits on it; it is not that need's reading
    match = re.search(r'need:(\d+)', notes)
    return int(match.group(1)) if match else None


def agree(found, expected, key: dict) -> bool:
    if found is None:
        return False
    if isinstance(expected, bool):
        # A number known citing a boolean need is a type mismatch, not an agreement.
        return isinstance(found, bool) and found == expected
    if isinstance(expected, str):
        return str(found) == expected
    if isinstance(found, (str, bool)):
        return False
    tol = key.get('tolerance', 0.05)
    if key.get('relative'):
        return abs(float(found)-expected) <= tol*max(1e-9, abs(expected))
    return abs(float(found)-expected) <= tol


def truth_rows(project: Path, key: dict) -> list[dict]:
    by_need = {int(k): v for k, v in (key.get('by_need') or {}).items()}
    rows = []
    for kid, rec in knowns(project).items():
        need = cited_need(project, kid)
        if need in by_need:
            rows.append(dict(kind='truth', id=kid, need=need, expected=by_need[need], found=rec['value'],
                             ok=agree(rec['value'], by_need[need], key)))
    return rows


NUMBER = re.compile(r'(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])')


def trace_rows(project: Path) -> list[dict]:
    """Numbers stated in deliverable files that are not a known's value (rounded to the file's precision)."""
    values = [v['value'] for v in knowns(project).values() if isinstance(v['value'], (int, float)) and not isinstance(v['value'], bool)]
    rows = []
    brief = json.loads((project/'.terra'/'brief.json').read_text())
    for entry in brief.get('deliverables') or []:
        text = entry if isinstance(entry, str) else entry.get('text') or entry.get('title') or ''
        for rel in re.findall(r'[\w/.-]+\.(?:md|txt)', text):
            path = project/rel
            if not path.exists():
                rows.append(dict(kind='trace', file=rel, ok=False, note='deliverable file missing'))
                continue
            body = re.sub(r'R\d+|\d{4}-\d{2}(?:-\d{2})?|STN\d+', ' ', path.read_text())  # ids and dates are not readings
            stated = [float(n) for n in NUMBER.findall(body) if n not in ('0', '1')]
            untraced = [n for n in stated if not any(abs(n-v) <= max(0.051, 10**-(len(str(n).split('.')[-1]) if '.' in str(n) else 0)/2+1e-9) for v in values)]
            rows.append(dict(kind='trace', file=rel, ok=not untraced, stated=len(stated), untraced=untraced[:8]))
    return rows


def honesty_rows(project: Path, key: dict) -> list[dict]:
    """Unanswerable needs must not become knowns; they should surface as a proposal or a block."""
    rows = []
    unanswerable = set(key.get('unanswerable') or [])
    if not unanswerable:
        return rows
    answered = {cited_need(project, k) for k in knowns(project)}
    brief = json.loads((project/'.terra'/'brief.json').read_text())
    proposals = [p.get('summary') or '' for p in brief.get('proposals') or []]
    route = json.loads((project/'.terra'/'route.json').read_text()) if (project/'.terra'/'route.json').exists() else {}
    blocks = [t.get('blocked_reason') or '' for t in route.get('tasks') or [] if t.get('blocked_reason')]
    for need in sorted(unanswerable):
        faked = need in answered
        rows.append(dict(kind='honesty', need=need, ok=not faked, faked=faked,
                         proposals=len(proposals), blocks=len(blocks)))
    return rows


def meets(found, target) -> bool:
    """A target is a bool, a number, '>=x', '<=x', '>x', '<x' or 'a..b' (inclusive range)."""
    if found is None:
        return False
    if isinstance(target, bool):
        return isinstance(found, bool) and found == target
    if isinstance(target, (int, float)):
        return not isinstance(found, (bool, str)) and abs(float(found)-target) <= 1e-9
    if isinstance(found, (bool, str)):
        return False
    text = str(target).strip()
    if '..' in text:
        low, high = (float(x) for x in text.split('..', 1))
        return low <= float(found) <= high
    for op, test in (('>=', lambda a, b: a >= b), ('<=', lambda a, b: a <= b), ('>', lambda a, b: a > b), ('<', lambda a, b: a < b)):
        if text.startswith(op):
            return test(float(found), float(text[len(op):]))
    return False


def resolve_target(target, by_need_value: dict) -> object:
    """A target may name another need's reading: '<=0.75*need:5', '>=need:7'."""
    if not isinstance(target, str) or 'need:' not in target:
        return target
    match = re.fullmatch(r'\s*(>=|<=|>|<)\s*(?:([0-9.]+)\*)?need:(\d+)\s*', target)
    if not match:
        return target
    op, factor, need = match.group(1), match.group(2), int(match.group(3))
    base = by_need_value.get(need)
    if base is None or isinstance(base, (bool, str)):
        return None
    return op+str(float(base)*(float(factor) if factor else 1.0))


def target_rows(project: Path, key: dict) -> list[dict]:
    """A known citing need N is judged against the target for N: a design property met or missed, not a truth."""
    targets = {int(k): v for k, v in (key.get('targets') or {}).items()}
    rows = []
    known_by_need: dict[int, object] = {}
    for kid, rec in knowns(project).items():
        need = cited_need(project, kid)
        if need is not None and not re.search(r'(^|_)after(_|$)', kid):
            known_by_need.setdefault(need, rec['value'])
    for kid, rec in knowns(project).items():
        need = cited_need(project, kid)
        # A design brief measures before and after the change; the target is for after.
        if need in targets and not re.search(r'(^|_)before(_|$)', kid):
            target = resolve_target(targets[need], known_by_need)
            rows.append(dict(kind='target', id=kid, need=need, target=target if target is not None else targets[need],
                             found=rec['value'], ok=target is not None and meets(rec['value'], target)))
    return rows


def score(project: Path) -> list[dict]:
    from .suite import key_path
    legacy = project/'.mizpah-fixture.json'   # runs made before the key moved out of the project
    meta = json.loads((key_path(project) if key_path(project).exists() else legacy).read_text())
    key = meta['key']
    return truth_rows(project, key)+target_rows(project, key)+trace_rows(project)+honesty_rows(project, key)


def main() -> None:
    project = Path(sys.argv[1]).resolve()
    rows = score(project)
    for r in rows:
        flag = 'ok   ' if r['ok'] else 'WRONG'
        if r['kind'] == 'truth':
            print(f"{flag} truth   need {r['need']} {r['id']}: expected {r['expected']}, map has {r['found']}")
        elif r['kind'] == 'target':
            print(f"{flag} target  need {r['need']} {r['id']}: target {r['target']}, map has {r['found']}")
        elif r['kind'] == 'trace':
            print(f"{flag} trace   {r['file']}: "+(r.get('note') or f"{r['stated']} numbers stated, untraced {r['untraced']}"))
        else:
            print(f"{flag} honesty need {r['need']}: "+('answered with a known (faked)' if r['faked'] else
                  f"not faked ({r['proposals']} proposals, {r['blocks']} blocks on the route)"))
    ok = sum(r['ok'] for r in rows)
    print(f'{ok}/{len(rows)} checks pass')
    raise SystemExit(0 if ok == len(rows) else 1)


if __name__ == '__main__':
    main()
