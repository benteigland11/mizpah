"""The controller: brief and observation in, unknowns, tasks and proposals out.

One bare model call per step and no tools. The controller never sees a worker transcript
and never writes to the map; it mints the error signal (unknowns, each citing the brief
entry it serves) and routes it (one task per unknown), and it may propose brief changes
with evidence. Everything it emits passes a structural guard before Terra sees it, and
proposals are only ever queued — the person accepts them.
"""
from __future__ import annotations

import json
import time
import os
from pathlib import Path
import re
from datetime import datetime, timezone
import subprocess
from typing import Any

from cg.bp_focused_agent_session_python.src import EndpointConfig, ModelClient, llama_model_client
from cg.backend_persistent_model_session_python.src.persistent_model_session import parse_turn

from . import briefs, capabilities, enablers, phases
from . import layout
from . import priorart
from .worker import terra

ID_PATTERN = re.compile(r'^[a-z][a-z0-9_]*$')
TYPES = ('number', 'boolean', 'label', 'formula', 'relation')
BUCKETS = ('low', 'medium', 'high')
OPEN_UNKNOWN = ('open', 'probing', 'blocked')
OPEN_TASK = ('ready', 'in_progress', 'blocked', 'pending')
BUDGET_BLOCK = ('worker budget exhausted', 'gate rounds exhausted')
# A block the loop wrote when its own plumbing raised: not a worker's finding about the source, and not a
# reason to rewrite the brief. A fresh run retries it. (The first embedded run's CR-001 proposed a need
# rewording over "driver error: empty file", 2026-09-20.)
DRIVER_BLOCK = 'driver error:'


DIGEST_SKIP = {'.terra', '.mizpah', '.git', '.venv', '__pycache__', 'node_modules', '.playbook', '.tool-output', '.session-history', 'cg'}
DIGEST_HEADS = ('README.md', 'CLAUDE.md', 'AGENTS.md', 'readme.md')


def repo_digest(project: Path, *, depth: int = 3, entries: int = 120, head_lines: int = 30) -> dict[str, Any]:
    """What a person glances at before splitting up work: the tree with sizes, the head of the README,
    the language mix. Deterministic and bounded; the controller buckets a task by how much of the
    method is unknown, and it cannot judge that blind to what already exists."""
    tree: list[str] = []
    counts: dict[str, int] = {}
    total = 0

    def walk(directory: Path, level: int) -> None:
        nonlocal total
        try:
            children = sorted(directory.iterdir(), key=lambda c: (c.is_file(), c.name))
        except OSError:
            return
        for child in children:
            if child.name in DIGEST_SKIP or child.name.startswith('.'):
                continue
            rel = child.relative_to(project).as_posix()
            if child.is_dir():
                inner = [c for c in child.rglob('*') if c.is_file() and not any(part in DIGEST_SKIP for part in c.parts)]
                if len(tree) < entries:
                    tree.append('  '*level+rel+'/ ('+str(len(inner))+' files)')
                if level < depth:
                    walk(child, level+1)
            else:
                total += 1
                counts[child.suffix or child.name] = counts.get(child.suffix or child.name, 0)+1
                if len(tree) < entries:
                    try:
                        size = child.stat().st_size
                    except OSError:
                        size = 0
                    tree.append('  '*level+rel+' ('+(str(size//1024)+' KB' if size >= 1024 else str(size)+' B')+')')
    walk(project, 0)
    heads = {}
    for name in DIGEST_HEADS:
        path = project/name
        if path.is_file():
            heads[name] = '\n'.join(path.read_text(errors='replace').splitlines()[:head_lines])
            break
    return dict(tree=tree, files=total, truncated=total > entries, kinds=dict(sorted(counts.items(), key=lambda kv: -kv[1])[:8]),
                heads=heads)


LOOK_PATHS = 6
LOOK_ROUNDS = 2
LOOK_LINES = 120
LOOK_CHARS = 9000


def read_looks(project: Path, paths: list[Any], looked: dict[str, str]) -> list[str]:
    """The controller asked to see files before deciding; the host reads them, bounded, and says what it
    refused. A person digs into a repo before splitting the work up; this is that, without a shell."""
    refused: list[str] = []
    budget = LOOK_CHARS
    for raw in list(paths)[:LOOK_PATHS]:
        rel = str(raw).strip()
        if not relative_path(rel) or not rel:
            refused.append('look '+repr(rel)+': not a relative path inside the project'); continue
        matches = sorted(project.glob(rel)) if any(ch in rel for ch in '*?[') else [project/rel]
        matches = [m for m in matches if m.is_file() and not any(part in DIGEST_SKIP for part in m.relative_to(project).parts)]
        if not matches:
            refused.append('look '+repr(rel)+': no such file'); continue
        for path in matches[:LOOK_PATHS]:
            key = path.relative_to(project).as_posix()
            if key in looked:
                continue
            try:
                lines = path.read_text(errors='replace').splitlines()
            except OSError as error:
                refused.append('look '+repr(key)+': '+str(error)); continue
            text = '\n'.join(lines[:LOOK_LINES])+('\n… ('+str(len(lines)-LOOK_LINES)+' more lines)' if len(lines) > LOOK_LINES else '')
            if len(text) > budget:
                text = text[:budget]+'\n… (cut at the look budget)'
            looked[key] = text
            budget -= len(text)
            if budget <= 0:
                refused.append('look budget spent; decide with what you have'); return refused
    return refused


def unknown_created(unknown: dict[str, Any]) -> str | None:
    """The file an unknown's task creates, from the notes Terra keeps (`creates X`)."""
    m = re.search(r'creates ([\w./-]+)', str(unknown.get('notes') or ''))
    return m.group(1) if m else None


def library_methods(config: dict[str, Any], brief: dict[str, Any]) -> list[dict[str, Any]]:
    """Procedures in the playbook near this brief, each with its reach: the host digs so the controller plans the
    route by method length and the worker opens what it is handed. One search per need and per deliverable;
    a hit counts when it shares two real words with the entry (the same bar as the worker's assignment)."""
    import subprocess as sp
    from .worker import STOP, shared_stems, BOOTSTRAP_PROCEDURES
    entries = [str(n) for n in (brief.get('needs') or [])] + [str(d) for d in (brief.get('deliverables') or [])]
    seen: dict[str, dict[str, Any]] = {}
    for text in entries:
        query = re.sub(r'[^a-z0-9 ]', ' ', text.lower())[:200]
        words = {w for w in re.findall(r'[a-z0-9]{4,}', query) if w not in STOP}
        if not words:
            continue
        try:
            out = sp.run([config['mizpah']['playbook'], 'search', query, '--limit', '4'], capture_output=True, text=True, timeout=60).stdout
            for hit in json.loads(out[out.find('{'):]).get('hits') or []:
                if hit['id'] in BOOTSTRAP_PROCEDURES or hit['id'] in seen:
                    continue
                blob = ' '.join(str(hit.get(k) or '') for k in ('id', 'title', 'description', 'snippet')).lower().replace('-', ' ')
                if shared_stems(words, blob) < 2:
                    continue
                seen[hit['id']] = dict(id=hit['id'], title=str(hit.get('title') or '')[:80])
        except (ValueError, OSError, sp.SubprocessError, KeyError):
            continue
    for pid, rec in seen.items():
        try:
            out = sp.run([config['mizpah']['playbook'], 'reach', pid], capture_output=True, text=True, timeout=60).stdout
            r = json.loads(out[out.find('{'):])
            rec.update(steps=int(r.get('steps') or 0), walks=int(r.get('walks') or 1), procedures=len(r.get('procedures') or {}),
                       gravity=int(r.get('gravity') or 0))
        except (ValueError, OSError, sp.SubprocessError):
            rec.update(steps=0, walks=1, procedures=1, gravity=0)
    return sorted(seen.values(), key=lambda r: r['id'])[:16]


def observe(config: dict[str, Any], project: Path) -> dict[str, Any]:
    """Everything the controller reads, bounded: the brief and a digest of the map and route."""
    brief = terra(config, project, 'brief', 'show')
    sitrep = terra(config, project, 'sitrep')
    # `known list --json` nests each known's fields under `record`.
    knowns = []
    for row in json.loads(subprocess.run([config['mizpah']['terra'], 'known', 'list', '--json'], cwd=project, env=dict(os.environ, **layout.terra_env(project)),
                                         capture_output=True, text=True).stdout or '[]'):
        record = dict(row.get('record') or row)
        record['stale'], record['stale_reasons'] = bool(row.get('stale')), list(row.get('stale_reasons') or [])
        knowns.append(record)
    unknowns = [json.loads(path.read_text()) for path in sorted((layout.map_root(project)/'unknowns').glob('*.json'))]
    route = terra(config, project, 'route', 'status')
    related_briefs = briefs.related(config, brief) if config['mizpah'].get('brief_library', True) else []
    registry = capabilities.render(config, brief)
    methods = library_methods(config, brief)
    observation = dict(
        methods=methods, playbook_store=str(config['mizpah'].get('playbook_store') or ''),
        project_path=str(project),
        repo=repo_digest(project),
        brief={key: brief.get(key) for key in ('title', 'version', 'status', 'mission', 'needs', 'deliverables',
                                                'non_goals', 'enablers', 'budget_points', 'phases', 'open_proposals')}
              | dict(proposals=[p for p in json.loads((project/layout.dirname(project)/'brief.json').read_text()).get('proposals') or []
                                if p.get('status') in (None, 'open', 'pending')],
                     decided=[p for p in json.loads((project/layout.dirname(project)/'brief.json').read_text()).get('proposals') or []
                              if p.get('status') in ('accepted', 'rejected')][-6:]),
        gate=sitrep.get('gate'), related_briefs=related_briefs, registry=registry, prior_art=[],
        budget=(sitrep.get('route') or {}).get('budget'),
        knowns=[dict(id=k.get('id'), type=k.get('type'), status=k.get('status'), confidence=k.get('confidence'),
                     n=(k.get('stats') or {}).get('n'), mean=(k.get('stats') or {}).get('mean'),
                     rate=(k.get('stats') or {}).get('rate'), mode=(k.get('stats') or {}).get('mode'), claim=k.get('claim'),
                     stale=k.get('stale', False), stale_reasons=k.get('stale_reasons') or []) for k in knowns],
        unknowns=[dict(id=u['id'], status=u.get('status'), type=u.get('type'), quantity=u.get('quantity'),
                       claim=u.get('claim'), resolved_by=u.get('resolved_by'), notes=u.get('notes')) for u in unknowns],
        tasks=[dict(id=t['id'], status=t['status'], unknown=t.get('map_id'),
                    unknowns=[t.get('map_id')]+[a.removeprefix('unknown:') for a in t.get('acceptance') or []
                                                if a.startswith('unknown:')],
                    bucket=t.get('bucket'), title=t['title'], blocked_reason=t.get('blocked_reason'))
               for t in route.get('tasks') or []],
    )
    # What the library holds for what is still owed, looked up per entry (no model): the controller's chance to
    # route an install instead of a build, or mint a reading the way another project did.
    if config['mizpah'].get('brief_library', True):
        try:
            _, owed = coverage(observation)
            observation['prior_art'] = priorart.render(config, project, owed)
        except Exception:  # noqa: BLE001 — a lookup never fails an observation
            observation['prior_art'] = []
    return observation


OPERATOR_NOTES = 'operator.jsonl'   # under the session root: {at, text, read?} — the person's replies to the loop's notices


def operator_notes(root: Path | None, *, unread_only: bool = True) -> list[dict[str, Any]]:
    """What the person said to this run (a reply to a stopped or completed notice, a comment on a live one)."""
    if root is None or not (root/OPERATOR_NOTES).exists():
        return []
    out = []
    for line in (root/OPERATOR_NOTES).read_text().splitlines():
        try:
            note = json.loads(line)
        except ValueError:
            continue
        if unread_only and note.get('read'):
            continue
        # A memo addressed to a worker (`to: worker:<task>`) is on the same record but is not the controller's.
        if str(note.get('to') or 'controller').startswith('worker:'):
            continue
        out.append(note)
    return out


def mark_notes_read(root: Path | None, notes: list[dict[str, Any]]) -> None:
    """A note is put before the controller once; the briefing that follows is its answer."""
    if root is None or not notes or not (root/OPERATOR_NOTES).exists():
        return
    ats = {n.get('at') for n in notes}
    lines = []
    for line in (root/OPERATOR_NOTES).read_text().splitlines():
        try:
            note = json.loads(line)
        except ValueError:
            continue
        if note.get('at') in ats:
            note['read'] = True
        lines.append(json.dumps(note))
    (root/OPERATOR_NOTES).write_text('\n'.join(lines)+'\n')


def reviewer_doubts(journal: Path) -> list[dict[str, Any]]:
    """What the check-in reviewer still doubted about a probe when its completion budget ran out, from the tasks
    of the loop's current cycle (loop.json beside the controller journal): the reading stands; the doubt is the
    controller's to turn into a reading of its own, or to drop."""
    try:
        loop = json.loads((Path(journal).parent/'loop.json').read_text())
        cycle = (loop.get('cycles') or [])[-1]
    except (OSError, ValueError, IndexError):
        return []
    out = []
    for t in cycle.get('tasks') or []:
        for d in t.get('reviewer_doubts') or []:
            out.append(dict(task=t.get('task'), unknowns=t.get('unknowns') or [], correction=str(d.get('correction') or '')[:300],
                            evidence=str(d.get('evidence') or '')[:300]))
    return out[:8]


def map_readings(state: Path, map_id: str) -> list[dict[str, Any]]:
    """Every quantity read on a task map, from its live runs: what a worker's probes put there whether or not an
    unknown asked for it. The controller composes from these and links a run before it mints a probe. One line
    per quantity: the latest value, how many runs read it, which probe."""
    if not map_id or map_id == 'global':
        return []
    runs_dir = state/'map'/'sessions'/map_id/'runs'   # `state` is the project's .mizpah (the session root's grandparent)
    if not runs_dir.is_dir():
        return []
    seen: dict[str, dict[str, Any]] = {}
    for meta in sorted(runs_dir.glob('*/meta.json')):
        try:
            run = json.loads(meta.read_text())
        except (OSError, ValueError):
            continue
        if run.get('voided') or run.get('status') != 'ok':
            continue
        for m in run.get('measures') or []:
            q = str(m.get('quantity') or '')
            if not q:
                continue
            entry = seen.setdefault(q, dict(quantity=q, probe=run.get('probe_id'), runs=0, value=None))
            entry['runs'] += 1
            entry['value'] = m.get('value')
    return sorted(seen.values(), key=lambda e: e['quantity'])


def task_workspaces(root: Path) -> list[dict[str, Any]]:
    """Past work orders, one per task that reached a turn: what each was for and what it left on disk (probes,
    walks, widgets) for a fresh worker to find."""
    out = []
    tasks = root/'tasks'
    if not tasks.is_dir():
        return out
    for d in sorted(tasks.iterdir()):
        if not (d/'state.sqlite3').exists():
            continue
        try:
            saved = json.loads((d/'task.json').read_text())
        except (OSError, ValueError):
            continue
        result = None
        try:
            result = json.loads((d/'result.json').read_text())
        except (OSError, ValueError):
            pass
        unknowns = [u.get('id') for u in saved.get('unknowns') or [] if isinstance(u, dict)]
        probes = sorted({p.name.removesuffix('_probe') for p in (root.parent.parent/'map'/'probes').glob('*_probe')
                         if (p/'measure.py').exists() and p.name.removesuffix('_probe') in unknowns}) if (root.parent.parent/'map').is_dir() else []
        walks, widgets = [], []
        try:
            for line in (d/'events'/'session.jsonl').read_text().splitlines():
                if 'playbook open ' in line:
                    # The procedure named on the open command, and nothing else on the line: an `"id"` regex
                    # over the whole line took every id near it — call ids, the procedures a search listed —
                    # and the sitrep said a worker had walked cad-gpu-headlight-ibl on a piano piece.
                    walks += [m for m in re.findall(r'playbook open (?:--\S+ )*([a-z][a-z0-9-]+)', line)
                              if m not in ('help',)]
                if '"playbook_open"' in line:
                    # The typed verb: the id is in that call's arguments, read from the call, not the line.
                    try:
                        calls = ((json.loads(line).get('payload') or {}).get('response') or {}).get('tool_calls') or []
                    except ValueError:
                        calls = []
                    for call in calls:
                        fn = call.get('function') or {}
                        if fn.get('name') == 'playbook_open':
                            try:
                                walks.append(str(json.loads(fn.get('arguments') or '{}').get('id') or ''))
                            except ValueError:
                                pass
                    walks = [w for w in walks if w]
                for m in re.findall(r'cg/([a-z0-9_]+)/src/', line):
                    widgets.append(m)
        except OSError:
            pass
        out.append(dict(task=d.name, unknowns=unknowns, verdict=(result or {}).get('verdict') or 'live',
                        turns=(result or {}).get('turns'), probes=probes, walks=sorted(set(walks))[:6],
                        widgets=sorted(set(widgets))[:6], readings=map_readings(root.parent.parent, saved.get('map') or ''),
                        walks_open=[dict(procedure=w.get('procedure'), unticked=w.get('unticked'), next=w.get('next'),
                                         continues=w.get('continues') or 0, next_from=w.get('next_from') or 0)
                                    for w in ((result or {}).get('walks_open') or [])][:8]))
    return out


def last_cautions(journal: Path) -> list[str]:
    """What the guard noted on the previous briefing: applied as decided, said once here."""
    try:
        lines = Path(journal).read_text().splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        try:
            return [str(c) for c in (json.loads(line).get('cautions') or [])][:12]
        except ValueError:
            continue
    return []


def render_observation(observation: dict[str, Any], mode: str, refusals: list[str] = ()) -> str:
    brief = observation['brief']
    lines = []
    if observation.get('cautions'):
        lines.append('# Cautions on your last briefing (applied as you decided; nothing to redo unless you agree)')
        lines += ['  - '+c[:300] for c in observation['cautions']]
        lines.append('')
    if observation.get('operator_notes'):
        # The person answered the loop (a reply to a notice): it is the first thing the controller reads, and the
        # briefing it writes is the answer. The brief itself moves only through a proposal.
        lines.append('# From the person (a reply to this run; answer it in this briefing — route what it asks, propose '
                     'what would change the brief, or say why nothing changes)')
        for note in observation['operator_notes']:
            lines.append('  '+time.strftime('%Y-%m-%d %H:%M', time.gmtime(float(note.get('at') or 0)))+': '+str(note.get('text') or '').strip()[:1200])
        lines.append('')
    if observation.get('reviewer_doubts'):
        # The check-in reviewer's leftover doubt about a probe, after the reading stood: not a task for the same
        # worker (it had its budget of send-backs); a candidate reading of its own, cited to the need it serves,
        # if the doubt is real — or nothing, if the reading already answers the need.
        lines.append('# What the check-in reviewer still doubted about a probe when the reading stood (a reading of its own, or nothing)')
        for d in observation['reviewer_doubts']:
            lines.append('  task '+str(d['task'])+' ('+', '.join(d['unknowns'])+'): '+d['correction']
                         +(' — evidence: '+d['evidence'] if d['evidence'] else ''))
        lines.append('')
    if observation.get('memory'):
        lines.append('# Your notes from last step (memory.md — what you were doing; the map is what is true)')
        lines += ['  '+ln for ln in str(observation['memory']).splitlines()]
    lines += ['# Brief (reference, v'+str(brief.get('version'))+', '+str(brief.get('status'))+')',
             'Mission: '+str(brief.get('mission'))]
    lines += phases.render(brief)
    lines += enablers.render(brief)
    lines += observation.get('registry') or []
    cited: dict[str, list[str]] = {}
    for u in observation['unknowns']:
        notes = str(u.get('notes') or '')
        ref = notes.split('cites ', 1)[1].split(';')[0].strip() if 'cites ' in notes else ''
        if ref:
            cited.setdefault(ref, []).append(u['id']+' ['+str(u.get('status'))+']: '+str(u.get('claim')))
    states, owed = coverage(observation)
    for key in ('needs', 'deliverables', 'non_goals'):
        entries = brief.get(key) or []
        lines.append(key.capitalize()+':'+('' if entries else ' (none)'))
        for i, entry in enumerate(entries):
            ref = key[:-1].replace('non_goal', 'non-goal')+':'+str(i+1)
            state = ('  ['+states[ref]+']') if ref in states else ''
            lines.append('  '+ref+' '+str(entry)+((phases.tag(brief, ref)+enablers.tag(brief, ref)) if key != 'non_goals' else '')+state)
            if key == 'deliverables':
                for line in cited.get(ref, []):
                    lines.append('      ↳ '+line[:160])
                if not cited.get(ref):
                    lines.append('      ↳ (no unknown cites this deliverable)')
    # The ledger of things the deliverables name and who makes them exist. A reading of a file nobody builds can
    # only block; the controller minted readers before builders on every brief that built something (2026-09-19).
    ledger = deliverable_ledger(observation)
    if ledger:
        lines.append('Deliverable files (what must exist before it can be read — route the builder first, readers depend on it):')
        lines += ['  '+line for line in ledger]
        unbuilt = [line.split(' — ')[0] for line in ledger if line.endswith('NOBODY BUILDS IT YET')]
        if len(unbuilt) > 1:
            lines.append('  Several are unbuilt: mint a builder for EACH of them in this reply (one unknown per file, or one '
                         'unknown per candidate set), not one per eval — each eval you spend on a single file is a worker '
                         'turn nobody needed.')
    if brief.get('budget_points') is not None:
        lines.append('Budget points: '+str(brief['budget_points']))
    proposals = brief.get('proposals') or []
    if proposals:
        lines.append('Open proposals (queued for the person; the map\'s record that a need cannot be met as written; '
                     'the project cannot be judged met while one is open):')
        for p in proposals:
            lines.append('  '+str(p.get('id'))+' '+str(p.get('summary') or '').split(' \u2014 evidence:')[0][:200]
                         +(' (the same ask as '+str(p['same_as'])+'; asking again adds nothing)' if p.get('same_as') else ''))
    decided = brief.get('decided') or []
    if decided:
        # What the person decided and why: a rejected proposal is not re-proposed, an accepted one is now the brief.
        lines.append('Decided proposals (the person\'s reasons; a rejected change is not proposed again in other words):')
        for p in decided:
            lines.append('  '+str(p.get('id'))+' '+str(p.get('status'))+': '+str(p.get('summary') or '').split(' \u2014 evidence:')[0][:120]
                         +(' — reason: '+str(p['decision_reason'])[:160] if p.get('decision_reason') else ''))
    if states:
        n_met = sum(1 for v in states.values() if v.startswith('MET') and 'FALSE' not in v)
        lines.append('Coverage: '+str(n_met)+' of '+str(len(states))+' entries met; owed: '
                     +(', '.join(ref for ref, _ in owed) if owed else 'none')+'.')
    lines += observation.get('prior_art') or []
    lines.append('')
    lines += briefs.render(observation.get('related_briefs') or [])
    lines.append('# Map (state)')
    lines.append('Gate: '+('green' if (observation.get('gate') or {}).get('ok') else 'red')+
                 ''.join('\n  - '+str(v.get('why') or v.get('kind')) for v in (observation.get('gate') or {}).get('violations') or []))
    repo = observation.get('repo') or {}
    if repo:
        lines.append('# Project files ('+str(repo.get('files', 0))+' files'+(', tree truncated' if repo.get('truncated') else '')+'; '
                     +', '.join(k+' '+str(v) for k, v in (repo.get('kinds') or {}).items())+')')
        lines += ['  '+t for t in repo.get('tree') or []] or ['  (empty)']
        for name, head in (repo.get('heads') or {}).items():
            lines.append('# '+name+' (head)')
            lines += ['  '+ln for ln in head.splitlines()]
    for name, text in (observation.get('looked') or {}).items():
        lines.append('# '+name+' (you asked to see this)')
        lines += ['  '+ln for ln in text.splitlines()]
    lines.append('Knowns:'+('' if observation['knowns'] else ' (none)'))
    claims = {u['id']: u.get('claim') for u in observation['unknowns']}
    for k in observation['knowns']:
        value = k['mean'] if k['mean'] is not None else k['rate']
        if k['type'] == 'label':
            value = repr(k.get('mode')) if k.get('mode') is not None else None
        elif k['type'] == 'boolean' and value is not None:
            value = 'true' if float(value) >= 0.5 else 'false'
        elif isinstance(value, float):
            value = round(value, 4)
        stale = ' STALE: '+'; '.join(str(r)[:80] for r in k['stale_reasons'][:2]) if k.get('stale') else ''
        # The claim is what has been read: without it the controller saw ids and numbers, could not tell that an
        # entry's clauses were already covered, and minted a validation per clause (changing-meter, three tasks
        # on deliverable 2's four clauses in one night).
        claim = str(claims.get(k['id']) or '').strip()
        lines.append('  '+str(k['id'])+' = '+str(value)+' ('+str(k['confidence'])+', n='+str(k['n'])+')'+stale+(': '+claim if claim else ''))
    if any(k.get('stale') for k in observation['knowns']):
        lines.append('A STALE known is no longer believed: a file it depends on changed after its readings. It is owed '
                     'again under the SAME id — route a task that lists the stale known\'s id among its unknowns; the '
                     'worker re-takes that reading with its probe. Never mint a new unknown for a claim the map already '
                     'holds: a second probe for the same question is shopping for an answer.')
    false_artifacts = [k for k in observation['knowns'] if k['type'] == 'boolean' and k.get('rate') is not None
                       and float(k['rate']) < 0.5 and not k.get('stale')]
    if false_artifacts:
        lines.append('A boolean that reads false about an artifact ('+', '.join(k['id'] for k in false_artifacts[:4])+') is '
                     'answered by a task that lists that same id: the worker changes the artifact and re-takes the reading '
                     '(voiding the false runs). Not a new unknown, not a new probe.')
    open_unknowns = [u for u in observation['unknowns'] if u['status'] in OPEN_UNKNOWN]
    resolved = len(observation['unknowns'])-len(open_unknowns)
    lines.append('Open unknowns:'+('' if open_unknowns else ' (none)')+(' — '+str(resolved)+' resolved' if resolved else ''))
    for u in open_unknowns:
        notes = str(u.get('notes') or '')
        reason = notes.split('blocked: ', 1)[1] if 'blocked: ' in notes else ''
        # The worker's reason usually opens with what it did resolve and ends with why the rest could not be.
        why = ' (blocked: '+(reason if len(reason) <= 400 else reason[:120]+' … '+reason[-260:])+')' if reason else ''
        lines.append('  '+u['id']+' ['+str(u['status'])+'] '+str(u['claim'])+why)
    if any('blocked: ' in str(u.get('notes') or '') for u in open_unknowns):
        lines.append('A blocked unknown has no task: its worker could not record the reading as the unknown is typed '
                     'or asked. If the type was wrong (a list measured where a number was asked, a name where a number '
                     'was), retype it: "retype": [{"unknown": "<id>", "type": "number|boolean|label", "claim": "<sharper '
                     'claim, optional>"}] — the same id, asked right, and its task is released. Otherwise propose the '
                     'change and leave it — the artifacts that depend on the map may still be built.')
    lines.append('Route tasks:'+('' if observation['tasks'] else ' (none)'))
    finished = [t for t in observation['tasks'] if t['status'] in ('done', 'cancelled')]
    if finished:
        # Closed tasks are history the map already shows as knowns: ids only.
        lines.append('  done: '+', '.join(t['id'] for t in finished))
    for t in observation['tasks']:
        if t in finished:
            continue
        reason = str(t.get('blocked_reason') or '')
        lines.append('  '+t['id']+' ['+t['status']+', '+str(t['bucket'])+'] → '+', '.join(t.get('unknowns') or [str(t['unknown'])])+': '+t['title']+
                     ((' (blocked by the harness, not the worker: '+reason+' — it is retried on the next run; nothing about the '
                       'source or the brief follows from it)') if reason.startswith(DRIVER_BLOCK) else
                      (' (blocked: '+reason+')' if reason else '')))
    worker_blocked = [t for t in observation['tasks'] if t not in finished and t.get('blocked_reason')
                      and not str(t['blocked_reason']).startswith(DRIVER_BLOCK)]
    if observation.get('workspaces'):
        lines.append('Past work orders and what they left on disk (a fresh worker finds it there; nothing of their windows carries):')
        for w in observation['workspaces']:
            lines.append('  '+w['task']+' ['+str(w['verdict'])+(', '+str(w['turns'])+' turns' if w.get('turns') else '')+'] → '
                         +', '.join(w['unknowns'])+(' · probes: '+', '.join(w['probes']) if w['probes'] else '')
                         +(' · walked: '+', '.join(w['walks']) if w['walks'] else '')+(' · widgets: '+', '.join(w['widgets']) if w['widgets'] else '')
                         +(' · walks left: '+'; '.join(str(x['procedure'])+' ('+str(x['unticked'])+' unticked)' for x in w['walks_open']) if w.get('walks_open') else ''))
            if w.get('readings'):
                lines.append('    readings on its map: '+'; '.join(str(r['quantity'])+'='+json.dumps(r['value'])[:24]+' (n='+str(r['runs'])+', '+str(r['probe'])+')'
                                                                 for r in w['readings'][:12]))
    waiting = [t for t in observation['tasks'] if t['status'] in ('ready', 'in_progress')]
    if waiting and mode == 'eval':
        lines.append('Already routed and waiting to run: '+', '.join(t['id'] for t in waiting)+' — they cover '
                     +', '.join(sorted({u for t in waiting for u in (t.get('unknowns') or [str(t.get('unknown'))])}))
                     +'. Route only what these leave uncovered; an empty reply is right when they cover everything still owed.')
    if any(str(t.get('blocked_reason') or '').startswith(BUDGET_BLOCK) for t in observation['tasks']):
        lines.append('A task blocked on budget resumes from where it stopped if you re-bucket it.')
    if any(t['status'] == 'blocked' and not str(t.get('blocked_reason') or '').startswith(BUDGET_BLOCK)
           for t in observation['tasks']):
        unbuilt = [t for t in observation['tasks'] if t['status'] == 'blocked'
                   and not str(t.get('blocked_reason') or '').startswith(DRIVER_BLOCK)
                   and re.search(r'does not exist|do not exist|not exist|missing|absent|no such|not present|only .* exist', str(t.get('blocked_reason') or ''), re.I)]
        if unbuilt:
            lines.append('Blocked on a source that does not exist yet ('+', '.join(t['id'] for t in unbuilt)+'): this is your '
                         'routing, not the brief — the reading was routed before anything built its source. Look at the '
                         'deliverable files above: if nobody builds it, mint the artifact unknown that creates it (boolean, '
                         '`creates`) with a task, then release the blocked task once with "unblock" after that task is done. '
                         'Do not release it before the builder has run, and do not mint the reading again under another name.')
        lines.append('A task blocked by its worker for any other reason means the source could not be read as the unknown '
                     'asks: the question needs a different source, or the brief needs to change. That is what '
                     'proposals are for; do not re-mint the same question.')
    budget = observation.get('budget') or {}
    if budget:
        lines.append('Points: budget '+str(budget.get('budget_points'))+', planned '+str(budget.get('points_plan'))+
                     ', done '+str(budget.get('points_done'))+', unallocated '+str(budget.get('points_remaining_budget'))+
                     '.')
    lines.append('')
    now = phases.current(brief)
    lines.append('Step: '+('a work order landed' if mode == 'eval' else 'the route is empty')+'. Answer the gate\'s red'
                 +(' (phase '+now['id']+')' if now else '')+'; an empty reply is right when the route already covers it.')
    if refusals:
        lines.append('')
        applied = observation.get('applied_so_far') or {}
        if any(applied.values()):
            lines.append('Applied from your previous reply (on the route now; do not send them again): '
                         +'; '.join(k+' '+', '.join(v) for k, v in applied.items() if v))
        lines.append('Your previous reply had items refused by the guard; resubmit only corrected items, or fewer:')
        lines += ['  - '+r for r in refusals]
    return '\n'.join(lines)+'\n'


def model_client(config: dict[str, Any]) -> ModelClient:
    spec = config['controller']
    if spec.get('provider') == 'subscription':
        from mizpah.providers import hosted_model_client
        return hosted_model_client(spec, config)
    endpoint = EndpointConfig(**spec['endpoint'])
    if spec.get('provider', 'direct_json') == 'llama_client':
        return llama_model_client(endpoint, known_issues=spec.get('known_issues'))
    return ModelClient(endpoint)


_last_reasoning: list[str] = ['']   # set by decide(); read by the journal writer right after


# ---- the controller's verbs: read-only, journaled, no cap (the policy says be quick; every step ends in a decision)

TERRA_READ_VERBS = {('known', 'show'), ('known', 'get'), ('known', 'list'), ('known', 'tree'), ('unknown', 'show'), ('unknown', 'list'),
                    ('run', 'show'), ('run', 'list'), ('route', 'status'), ('route', 'log'), ('gate',), ('map', 'list'),
                    ('map', 'status'), ('sitrep',), ('brief', 'show'), ('probe', 'list'), ('probe', 'show')}
TOOL_RESULT_CHARS = 12000
TOOL_CALL_CEILING = 60   # a safety, not a budget: a step that reads sixty things is not deciding
MEMORY_CHARS = 4000

CONTROLLER_TOOLS = [
    dict(type='function', function=dict(name='terra', description='A read-only terra command against the project: known show <id>, '
         'known get <id>, known list, known tree <id>, unknown show <id>, unknown list, run show <id>, run list, route status, route log, '
         'gate [--map <id>], map list, map status, sitrep, brief show, probe list, probe show <id>. JSON back.',
         parameters=dict(type='object', properties=dict(args=dict(type='string', description='the words after `terra`')), required=['args']))),
    dict(type='function', function=dict(name='read', description='Numbered lines of a project file (200 at a time).',
         parameters=dict(type='object', properties=dict(path=dict(type='string'), offset=dict(type='integer', description='1-based first line')), required=['path']))),
    dict(type='function', function=dict(name='brief_read', description='A related brief from the library by title: its mission, needs, deliverables and the unknowns it resolved.',
         parameters=dict(type='object', properties=dict(title=dict(type='string')), required=['title']))),
    dict(type='function', function=dict(name='result', description='What a landed work order reported: verdict, turns, knowns, problems, what it minted, its last words.',
         parameters=dict(type='object', properties=dict(task=dict(type='string')), required=['task']))),
]


def run_tool(config: dict[str, Any], project: Path, root: Path | None, name: str, args: dict[str, Any]) -> str:
    """One verb, read-only, its result as text bounded for the window."""
    try:
        if name == 'terra':
            words = [w for w in str(args.get('args') or '').split() if w]
            head = tuple(words[:2]) if len(words) >= 2 and (words[0], words[1]) in TERRA_READ_VERBS else tuple(words[:1])
            if head not in TERRA_READ_VERBS or any(w in ('--force', '>', '|', ';', '&&') for w in words):
                return 'refused: `terra '+' '.join(words)[:80]+'` is not a read verb of the controller (known/unknown/run/probe show|list, route status|log, gate, map list|status, sitrep, brief show)'
            text = json.dumps(terra(config, project, *words), indent=1)
        elif name == 'read':
            rel = str(args.get('path') or '').strip()
            if not relative_path(rel) or not (project/rel).is_file():
                return 'refused: '+repr(rel)+' is not a file inside the project'
            lines = (project/rel).read_text(errors='replace').splitlines()
            start = max(1, int(args.get('offset') or 1))
            chunk = lines[start-1:start-1+200]
            text = '\n'.join(f'{start+i:6d}\t{ln}' for i, ln in enumerate(chunk))+('' if start-1+200 >= len(lines) else f'\n… ({len(lines)-(start-1+200)} more lines; offset {start+200})')
        elif name == 'brief_read':
            title = str(args.get('title') or '').strip().lower()
            docs = [d for d in briefs.stored(config) if str(d.get('title') or '').strip().lower() == title]
            if not docs:
                return 'no stored brief titled '+repr(title)
            d = docs[-1]
            text = json.dumps(dict(title=d.get('title'), mission=d.get('mission'), needs=d.get('needs'), deliverables=d.get('deliverables'),
                                   outcome=d.get('stop'), tasks=d.get('tasks'), unknowns=d.get('unknowns')), indent=1)
        elif name == 'result':
            tid = str(args.get('task') or '').strip()
            path = (root/'tasks'/tid/'result.json') if root else None
            if not path or not path.exists() or not re.fullmatch(r'[a-z][a-z0-9_]*', tid):
                return 'no landed work order '+repr(tid)
            r = json.loads(path.read_text())
            text = json.dumps({k: r.get(k) for k in ('verdict', 'turns', 'knowns', 'runs', 'problems', 'blocked_reason', 'playbook', 'widgets', 'reviewer_doubts')}
                              | dict(final_text=(r.get('rounds') or [{}])[-1].get('final_text', '')[:2000]), indent=1)
        else:
            return 'refused: no such verb '+repr(name)
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        return 'error: '+str(error)[:500]
    return text if len(text) <= TOOL_RESULT_CHARS else text[:TOOL_RESULT_CHARS]+'\n… (cut at '+str(TOOL_RESULT_CHARS)+' characters)'


_last_tools: list[list[dict[str, Any]]] = [[]]


def decide(client: ModelClient, config: dict[str, Any], system: str, user: str,
           project: Path | None = None, root: Path | None = None) -> tuple[dict[str, Any], str]:
    """The controller's step: read what it needs with its verbs, then one JSON decision. Every call is kept for
    the journal (`_last_tools`); the model ends the step by replying without a tool call."""
    messages = [dict(role='system', content=system), dict(role='user', content=user)]
    calls: list[dict[str, Any]] = []
    _last_tools[0] = calls
    started = time.time()
    seen: set[str] = set()
    ceiling = TOOL_CALL_CEILING
    # Reads that brought nothing: a refused verb or a read already made. Three in a row is a step that has stopped
    # learning and is told to decide — the same rule as a worker's rounds that fix nothing. Ornith made 21 reads in
    # five minutes on an empty map (landing, 2026-09-22): seven refused shell pipes and invented ids, `probe list`
    # three times. A model that reads like a person never trips it.
    empty_run = 0

    def live(phase: str) -> None:
        # The step as it stands, for a watcher: the journal gets the record when the step ends, and a first step on
        # a local model can be ten minutes of reads and thinking with nothing on disk to show it is alive.
        if root is None:
            return
        try:
            (root/'controller.live.json').write_text(json.dumps(dict(
                phase=phase, started_at=started, at=time.time(), model_calls=len([m for m in messages if m.get('role') == 'assistant'])+(1 if phase == 'model' else 0),
                user=user.split('\n', 1)[0][:200], tools=calls), ensure_ascii=False))
        except OSError:
            pass
    while True:
        payload = dict(config['controller']['generation'], messages=messages,
                       max_tokens=config['mizpah'].get('controller_output_tokens', 8192))
        if project is not None:
            payload['tools'] = CONTROLLER_TOOLS
        live('model')
        response = client.complete(payload, 'controller')
        message = parse_turn(response).message
        wanted = message.get('tool_calls') or []
        if not wanted or project is None:
            break
        messages.append(dict(role='assistant', content=message.get('content') or '', tool_calls=wanted))
        for call in wanted:
            fn = call.get('function') or {}
            try:
                args = json.loads(fn.get('arguments') or '{}')
            except ValueError:
                args = {}
            key = json.dumps([fn.get('name'), args], sort_keys=True)
            if key in seen:
                # The same read again: nothing on disk changed between two reads in one step. A small model asked
                # `probe list` three times on an empty map (landing, Ornith, 2026-09-22), reading "[]" as a page to
                # come back to; the answer is the one it already has.
                text = 'the same read as before; nothing changed. Its answer is above — decide with what you have.'
            else:
                text = run_tool(config, project, root, str(fn.get('name')), args if isinstance(args, dict) else {})
                seen.add(key)
            refused = text.startswith(('refused', 'error', 'no ', 'the same read'))
            calls.append(dict(name=fn.get('name'), args=args, chars=len(text), refused=refused))
            messages.append(dict(role='tool', tool_call_id=call.get('id'), name=fn.get('name'), content=text))
            empty_run = empty_run+1 if refused else 0
            live('tools')
        if len(calls) >= ceiling or empty_run >= 3:
            messages.append(dict(role='user', content=('That is '+str(ceiling)+' reads' if len(calls) >= ceiling else
                                 'The last three reads brought nothing new')+'; decide now with what you have.'))
            payload = dict(config['controller']['generation'], messages=messages, max_tokens=config['mizpah'].get('controller_output_tokens', 8192))
            message = parse_turn(client.complete(payload, 'controller')).message
            break
    content = (message.get('content') or '').strip()
    # The model's own account of why, when the provider returns one (a reasoning summary, or a local
    # model's thinking). Kept beside the decision so a person can read the controller's mind.
    _last_reasoning[0] = (message.get('reasoning_content') or '').strip()
    start, end = content.find('{'), content.rfind('}')
    if start < 0 or end < start:
        raise ValueError('Controller reply held no JSON object: '+content[:300])
    try:
        decision = json.loads(content[start:end+1])
    except ValueError:
        # An object followed by a second one or a prose trailer: the first complete object is the decision.
        decision, _ = json.JSONDecoder().raw_decode(content[start:])
    if not isinstance(decision, dict):
        raise ValueError('Controller reply was not a JSON object')
    return decision, content


def relative_path(value: str) -> bool:
    return bool(value) and not value.startswith('/') and '..' not in Path(value).parts


def source_exists(project: Path, source: str) -> bool:
    """A source is a path or glob in the project, or the word `environment`; nothing else can be read."""
    if source == 'environment':
        return True
    if not relative_path(source):
        return False
    if any(ch in source for ch in '*?['):
        return any(True for _ in project.glob(source))
    return (project/source).exists()


def coverage(observation: dict[str, Any]) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Computed, not judged: for every brief entry, MET (a known at med or better answers it), OWED (an unknown is
    open — routed in which task, or unrouted), or UNCOVERED (nothing cites it). Returns the state line per entry
    ref and the owed/uncovered entries (ref, text) for the prior-art lookup. The controller reasoned this out from
    the raw lists on every step; a third of its evals were spent restating it."""
    brief = observation['brief']
    knowns = {k['id']: k for k in observation['knowns']}
    task_of: dict[str, str] = {}
    for t in observation['tasks']:
        for u in (t.get('unknowns') or [str(t.get('unknown'))]):
            task_of.setdefault(u, t['id']+' ['+t['status']+']')
    by_ref: dict[str, list[dict[str, Any]]] = {}
    for u in observation['unknowns']:
        notes = str(u.get('notes') or '')
        for kind, index in re.findall(r'(need|deliverable):(\d+)', notes.split(';')[0]):
            by_ref.setdefault(kind+':'+index, []).append(u)
    order = {'high': 3, 'med': 2, 'low': 1}
    states: dict[str, str] = {}
    owed: list[tuple[str, str]] = []
    for key in ('needs', 'deliverables'):
        for i, text in enumerate(brief.get(key) or [], start=1):
            ref = key[:-1]+':'+str(i)
            us = by_ref.get(ref, [])
            met, open_ = [], []
            for u in us:
                k = knowns.get(u['id'])
                if u.get('status') == 'resolved' and k and order.get(str(k.get('confidence')), 0) >= 2 and not k.get('stale'):
                    value = k.get('mean') if k.get('mean') is not None else (k.get('rate') if k.get('rate') is not None else k.get('mode'))
                    bad = k.get('type') == 'boolean' and k.get('rate') is not None and float(k['rate']) < 0.5
                    met.append(u['id']+'='+str(value)+(' FALSE' if bad else ''))
                else:
                    open_.append(u['id']+(' → '+task_of[u['id']] if u['id'] in task_of else ' (unrouted)'))
            if not us:
                states[ref] = 'UNCOVERED'
                owed.append((ref, str(text)))
            elif open_:
                states[ref] = 'OWED: '+', '.join(open_)+((' | met: '+', '.join(met)) if met else '')
                owed.append((ref, str(text)))
            else:
                states[ref] = 'MET: '+', '.join(met)
                if any(m.endswith('FALSE') for m in met):
                    owed.append((ref, str(text)))
    return states, owed


def deliverable_ledger(observation: dict[str, Any]) -> list[str]:
    """Each file a deliverable names: built, or which unknown/task creates it, or nobody."""
    project = observation.get('project_path')
    creators: dict[str, tuple[str, str]] = {}
    for u in observation['unknowns']:
        notes = str(u.get('notes') or '')
        if 'creates ' in notes and '; enabler ' not in notes:
            made = notes.split('creates ', 1)[1].split(';')[0].strip()
            task = next((t['id']+' ['+t['status']+']' for t in observation['tasks'] if u['id'] in (t.get('unknowns') or [])), '(no task)')
            creators[made.lower()] = (u['id'], task)
    lines = []
    seen = set()
    for index, text in enumerate(observation['brief'].get('deliverables') or [], start=1):
        for path in re.findall(r'`([^`]+)`|([\w./-]+\.[A-Za-z0-9]{1,5})', str(text)):
            name = (path[0] or path[1]).strip().lstrip('/')
            if not name or ' ' in name or name in seen or '.' not in name.rsplit('/', 1)[-1]:
                continue
            seen.add(name)
            low = name.lower()
            exists = bool(project) and (Path(project)/name.split('<')[0].rstrip('/')).exists() if '<' not in name else False
            exists = exists or (bool(project) and '<' in name and any(Path(project).glob(name.replace('<candidate>', '*').replace('<function>', '*'))))
            maker = next((v for k, v in creators.items() if k == low or k.endswith('/'+low.rsplit('/', 1)[-1]) or low.startswith(k)), None)
            state = 'built' if exists else ('creates: '+maker[0]+' → '+maker[1] if maker else 'NOBODY BUILDS IT YET')
            lines.append('deliverable:'+str(index)+' '+name+' — '+state)
    return lines


def uncovered_deliverable_terms(observation: dict[str, Any], extra_unknowns: list[dict[str, Any]] = (), *,
                                open_phases_only: bool = False) -> list[str]:
    """Backticked names in each deliverable that no unknown citing it mentions.

    The brief's own backticks are its vocabulary of named things (commands, files, invocations);
    a deliverable is covered only when each of them appears in the id or claim of some unknown
    that cites that deliverable. Mechanical, so a broad claim cannot paper over a missing command.
    """
    problems: list[str] = []
    unknowns = list(observation['unknowns'])+[dict(id=u['id'], claim=u['claim'], notes='cites '+u['cites']) for u in extra_unknowns]
    for index, text in enumerate(observation['brief'].get('deliverables') or [], start=1):
        ref = 'deliverable:'+str(index)
        if open_phases_only and phases.refused_cites(observation['brief'], [ref]):
            continue   # a later phase's deliverable is not owed yet
        citing_unknowns = [u for u in unknowns if ('cites '+ref) in str(u.get('notes') or '')]
        if not citing_unknowns:
            # No backticks does not mean nothing named: a deliverable no unknown cites is not covered at all.
            problems.append(ref+' has no unknown citing it (the artifact it names has not been made or verified)')
            continue
        citing = ' '.join((u['id']+' '+str(u.get('claim') or '')).lower() for u in citing_unknowns)
        terms = [t for t in re.findall(r'`([^`]+)`', text) if t and len(t) <= 60]
        def covered(term: str) -> bool:
            low = term.lower()
            if low in citing:
                return True
            # `python3 -m weather <command>`: a placeholder names a family; the invocation prefix must appear.
            if '<' in low:
                prefix = low.split('<')[0].strip()
                return bool(prefix) and prefix in citing
            return term.split()[-1].strip('<>').lower() in citing
        missing = [t for t in terms if not covered(t)]
        if missing:
            problems.append(ref+' names '+', '.join('`'+m+'`' for m in missing)+' but no unknown citing it mentions them')
    return problems


RETRY_SUFFIX = re.compile(r'(_v\d+|_current|_again|_fix(ed)?|_retry|_redo|_\d+)+$')


SPEC_CLAIM = re.compile(r'\b(meets|satisf(?:y|ies)|fulfil+s?|conforms? to|matches?|honou?rs?)\b[^.]{0,40}\b(spec|specification|brief|requirements?|request(?:ed)?)\b'
                        r'|\b(is|are) (valid|correct|complete|acceptable|as requested|as specified)\b|\bas (requested|specified|described)\b',
                        re.I)


def _measured_since_change(project: Path, known: dict[str, Any], refs: list[str], brief: dict[str, Any]) -> str:
    """When the known's readings postdate every file the cited brief entries name, the reading is current:
    returns when it was taken (ISO) — empty when a named file is newer, none is named, or the known has no runs."""
    run_ids = [r.get('run_id') or '' for r in ((known.get('stats') or {}).get('by_run') or [])] or list(known.get('run_ids') or [])
    stamps = sorted(m.group(0) for r in run_ids for m in [re.match(r'\d{8}T\d{6}Z', str(r))] if m)
    if not stamps:
        return ''
    taken = datetime.strptime(stamps[-1], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
    named: list[Path] = []
    for ref in refs:
        kind, _, index = ref.partition(':')
        entries = brief.get('deliverables' if kind == 'deliverable' else 'needs') or []
        text = entries[int(index)-1] if index.isdigit() and 0 < int(index) <= len(entries) else ''
        for n in re.findall(r'`([^`]+)`|([\w./-]+\.[A-Za-z0-9]{1,5})', str(text)):
            for name in n:
                if name and (project/name).is_file():
                    named.append(project/name)
    if not named:
        return ''
    changed = max(datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc) for p in named)
    return taken.strftime('%Y-%m-%dT%H:%M:%SZ') if taken > changed else ''


def _same_reading(a: str, b: str) -> bool:
    """Two claims are one reading when their content words (stemmed) overlap almost entirely."""
    wa, wb = briefs._stems(briefs._words(a)), briefs._stems(briefs._words(b))
    if len(wa) < 3 or len(wb) < 3:
        return False
    return len(wa & wb)/min(len(wa), len(wb)) >= 0.85


def stem(unknown_id: str) -> str:
    """`repair_docs_handwritten_v2`, `..._current` and `..._again` are one reading asked three times."""
    return RETRY_SUFFIX.sub('', unknown_id)


def _family(unknown_id: str) -> str:
    """Unknown ids that differ only by a number are one reading over a list: candidate_3_length → candidate_N_length."""
    return re.sub(r'(?<![a-z])\d+(?![a-z])', 'N', unknown_id)


def _merge_sibling_tasks(tasks: list[dict[str, Any]], cautions: list[str]) -> list[dict[str, Any]]:
    """Ten tasks that each read one row of the same table are one task read ten times: a worker session per
    row cost headline2 ten sessions of ~25 turns for "candidate N has its note". When three or more tasks
    carry nothing but members of one family (ids equal after numbers are masked), they become the first task,
    which takes every sibling and the union of their deps; the others are dropped and the merge is recorded."""
    by_family: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        families = {_family(u) for u in task['unknowns']}
        if len(families) == 1 and any(ch.isdigit() for ch in ''.join(task['unknowns'])):
            by_family.setdefault(next(iter(families)), []).append(task)
    dropped: set[str] = set()
    for family, members in by_family.items():
        if len(members) < 3:
            continue
        head, rest = members[0], members[1:]
        for task in rest:
            head['unknowns'] = head['unknowns']+[u for u in task['unknowns'] if u not in head['unknowns']]
            head['deps'] = head['deps']+[d for d in task['deps'] if d not in head['deps'] and d != head['id']]
            dropped.add(task['id'])
        head['deps'] = [d for d in head['deps'] if d not in dropped]
        head['title'] = re.sub(r'\b\d+\b', 'every', head['title'], count=1) if re.search(r'\b\d+\b', head['title']) else head['title']
        cautions.append('tasks '+', '.join(t['id'] for t in rest)+': readings that differ only by an index ('+family
                        +') are one reading over the list; merged into '+head['id']+', which now resolves all '
                        +str(len(head['unknowns']))+' of them in one session')
    kept = [t for t in tasks if t['id'] not in dropped]
    for task in kept:
        task['deps'] = [d for d in task['deps'] if d not in dropped]
    return kept

def _procedure_exists(procedure_id: str, store: str) -> bool:
    return bool(store) and (Path(store)/(procedure_id+'.json')).is_file()


def guard(decision: dict[str, Any], observation: dict[str, Any], project: Path | None = None, *,
          require_deliverables: bool = False) -> tuple[dict[str, Any], list[str]]:
    """Structural floor: every unknown cites a brief entry and names a source that exists; every task
    resolves an open unknown. The quantity is the unknown id — the controller does not pick a second name."""
    refusals: list[str] = []
    # Restatements: things the route already holds, listed again. Not refusals — nothing wrong was asked and
    # nothing changes — so they neither count nor cost a resubmission; a third of all "refusals" were these.
    noted: list[str] = []
    # Refused unknowns by id → the index of their refusal line, so a task that only carried them is folded
    # into that line instead of refused again ("neither minted here nor open" was a quarter of refusals).
    refused_unknowns: dict[str, int] = {}
    brief = observation['brief']
    counts = dict(need=len(brief.get('needs') or []), deliverable=len(brief.get('deliverables') or []))
    existing_unknowns = {u['id']: u for u in observation['unknowns']}
    knowns_by_id = {k['id']: k for k in observation.get('knowns') or []}
    dropped_duplicates: dict[str, str] = {}   # minted id -> the resolved, still-fresh reading it duplicated
    reopened: dict[str, str] = {}   # original id -> the id the decision used for its re-measure
    # Method guards became cautions (2026-09-20): the decision is applied as made and the caution rides with it
    # into the journal and the next briefing. A refusal costs a resubmission round; today's tally put 70 of 93
    # refusals on the guard being wrong about artifacts, their readings and the order between them, and not one
    # caught what the guards were written for. Terra refuses what must be refused at the tool (a completion
    # without a run, a known without one); the controller guard keeps only the brief's integrity hard.
    cautions: list[str] = []
    existing_tasks = {t['id'] for t in observation['tasks']}
    open_unknowns = {u['id'] for u in observation['unknowns'] if u['status'] in OPEN_UNKNOWN}
    # A task this decision takes off the route (a ready or blocked one, with a reason) frees its unknowns for the
    # tasks the same decision routes: the controller that replaced a blocked validation with the repair it called
    # for was refused ("already has an open task") and the loop declared it stalled (syncopation, 2026-09-21).
    leaving = {str(c.get('task')) for c in decision.get('cancel') or [] if isinstance(c, dict) and str(c.get('why') or '').strip()
               and next((t for t in observation['tasks'] if t['id'] == str(c.get('task'))), {}).get('status') in ('ready', 'blocked')}
    routed = {u for t in observation['tasks'] if t['status'] in OPEN_TASK and t['id'] not in leaving
              for u in (t.get('unknowns') or [t['unknown']])}
    unknowns, tasks, proposals, rebucket, unblock, retype = [], [], [], [], [], []
    for item in decision.get('unknowns') or []:
        if not isinstance(item, dict):
            refusals.append('unknown entry is not an object'); continue
        uid = str(item.get('id') or '')
        # One unknown may serve several brief entries ("the report states the questions the data could not
        # answer" cites the deliverable and the needs it names); the first reference is the primary cite.
        refs = [r.strip() for r in re.split(r'[|,;]| and ', str(item.get('cites') or '')) if r.strip()]
        if not refs:
            # A builder that names a deliverable's file has said what it cites; the field is inferred rather than
            # refused (the controller saw the ledger, minted the builders, and left `cites` empty — logo_mark4).
            text = (str(item.get('claim') or '')+' '+str(item.get('creates') or '')+' '+uid.replace('_', ' ')).lower()
            for index, dtext in enumerate(brief.get('deliverables') or [], start=1):
                names = [n.lower() for n in re.findall(r'`([^`]+)`|([\w./-]+\.[A-Za-z0-9]{1,5})', str(dtext)) for n in n if n]
                if any(n.rsplit('/', 1)[-1] in text or n.rsplit('/', 1)[-1].replace('.', ' ').replace('-', ' ') in text for n in names):
                    refs = ['deliverable:'+str(index)]
                    break
        cites = refs[0] if refs else ''
        kind, _, index = cites.partition(':')
        if not ID_PATTERN.match(uid):
            refusals.append('unknown '+repr(uid)+': id must match ^[a-z][a-z0-9_]*$'); continue
        if uid in existing_unknowns:
            noted.append('unknown '+uid+': already on the map ('+str(existing_unknowns[uid]['status'])+')'); continue
        # The same reading minted a third time under a new suffix is a loop, not a plan: two attempts that came
        # back false or blocked mean the source or the brief is wrong, and that is a proposal (docs_page minted
        # repair_docs_handwritten_compliance, _v2 and _current in a row, 2026-09-19).
        same_claim = [u for u in existing_unknowns.values() if u['id'] != uid and u.get('type') == item.get('type')
                      and _same_reading(str(u.get('claim') or ''), str(item.get('claim') or ''))]
        if same_claim and same_claim[0].get('status') == 'resolved' and same_claim[0]['id'] not in reopened:
            # A resolved reading minted again (pedal_timing_valid_after_fix, _current) is a re-measure of the original:
            # the artifact was repaired and the reading must be taken afresh. Reopen its own id and route on it rather
            # than refuse — refusing twice stalled the pedal gym's controller (2026-09-20). Unless the reading is
            # already newer than the artifact: a re-measure of a re-measure (changing-meter reopened a known adopted
            # nine minutes after meters.md last changed, right after the task that re-took it landed).
            original = same_claim[0]['id']
            fresh = _measured_since_change(project, knowns_by_id.get(original) or {}, refs, brief)
            if fresh:
                noted.append('unknown '+uid+': the map holds this reading as '+original+' [resolved], taken '+fresh+
                             '; nothing has changed since — not reopened, tasks naming '+uid+' drop it')
                dropped_duplicates[uid] = original
                continue
            reopened[original] = uid
            noted.append('unknown '+uid+': the map holds this reading as '+original+' [resolved]; reopened '+original+
                         ' to be measured again — tasks naming '+uid+' route on '+original)
            continue
        if same_claim:
            # Not resolved yet: the reading is already on its way. The task routes on the existing id instead.
            reopened.setdefault(same_claim[0]['id'], uid)
            cautions.append('unknown '+uid+': the map already holds this reading as '+same_claim[0]['id']+' ['+str(same_claim[0]['status'])
                            +']; tasks naming '+uid+' route on it'); continue
        twins = [u for u in existing_unknowns.values() if stem(u['id']) == stem(uid) and u['id'] != uid]
        if len(twins) >= 2:
            refusals.append('unknown '+uid+': the third attempt at '+stem(uid)+' ('+', '.join(t['id'] for t in twins)+' already exist); '
                            'a reading that failed twice is not re-minted — propose the change to the need or deliverable '
                            'it cites, with the two readings as evidence, or leave it'); continue
        def _bad(r: str) -> bool:
            kind, _, rest = r.partition(':')
            if kind == 'unknown':   # transitional: served by the unknown that cannot be resolved until this one is
                return rest not in existing_unknowns and rest not in {str(x.get('id')) for x in decision.get('unknowns') or [] if isinstance(x, dict)}
            return kind not in counts or not rest.isdigit() or not 1 <= int(rest) <= counts[kind]
        bad_refs = [r for r in refs if _bad(r)]
        if not refs or bad_refs:
            refusals.append('unknown '+uid+': cites '+repr(item.get('cites'))+' but the brief has '+str(counts['need'])+
                            ' needs and '+str(counts['deliverable'])+' deliverables; cite need:N, deliverable:N, or unknown:<id> '
                            'for the unknown this one must be resolved before (several allowed, separated by |)'); continue
        later = phases.refused_cites(brief, refs)
        if later:
            refusals.append('unknown '+uid+': '+'; '.join(later)); continue
        enabler = str(item.get('enabler') or '').strip()
        table = enablers.by_id(brief)
        if enabler:
            if enabler not in table:
                refusals.append('unknown '+uid+': enabler '+repr(enabler)+' is not declared in the brief ('+', '.join(table) or 'none'+')'); continue
            if str(table[enabler].get('status') or 'needed') in enablers.LIVE:
                refusals.append('unknown '+uid+': enabler '+enabler+' is already '+str(table[enabler]['status'])); continue
            if item.get('type') != 'boolean':
                refusals.append('unknown '+uid+': an enabler unknown is boolean — the instrument exists and validates'); continue
            if not item.get('creates') and table[enabler].get('path'):
                item = dict(item, creates=table[enabler]['path'])
        else:
            waiting = enablers.waiting(brief, refs)
            if waiting:
                refusals.append('unknown '+uid+': '+'; '.join(waiting)); continue
        item = dict(item, cites=cites, also=refs[1:], enabler=enabler)
        if item.get('type') not in TYPES:
            refusals.append('unknown '+uid+': type must be one of '+', '.join(TYPES)); continue
        if item.get('type') == 'formula':
            expression, variables = str(item.get('expression') or '').strip(), item.get('vars')
            if not expression or not isinstance(variables, dict) or not variables:
                refusals.append('unknown '+uid+': a formula needs "expression" and "vars" ({name: "known:<id>" or a run quantity}) — '
                                'the need\'s known composed from readings on the map'); continue
            item = dict(item, expression=expression, vars={str(k): str(v) for k, v in variables.items()})
        if item.get('type') == 'relation' and not str(item.get('x_quantity') or '').strip():
            refusals.append('unknown '+uid+': a relation names its x ("x_quantity", and "x_unit" if it has one)'); continue
        claim, evidence_needed = str(item.get('claim') or '').strip(), str(item.get('evidence_needed') or '').strip()
        source = str(item.get('source') or '').strip()
        creates = str(item.get('creates') or '').strip()
        if not claim:
            refusals.append('unknown '+uid+': claim is required'); continue
        if SPEC_CLAIM.search(claim) or (item.get('type') == 'boolean' and claim.count(',')+claim.count(' and ')+claim.count(';') >= 3):
            # A caution, not a refusal (the guard is hard only for brief integrity): a boolean that is the spec
            # itself makes one probe carry the whole need, and the check-in then keeps finding a clause it does
            # not measure — the worker spent forty turns after Terra had the task done (counting-a-bar, 2026-09-21).
            cautions.append('unknown '+uid+': the claim reads as the whole specification in one boolean ('+claim[:80]+'). One '
                            'reading per quantity: the builder\'s unknown is that the file exists and parses; each thing the '
                            'entry says about it (tempo, meter, sections, spacing) is an unknown of its own, cited to the '
                            'need that says it, so no single probe has to check everything')
        if not evidence_needed or evidence_needed.lower() in ('true', 'false', 'none', 'yes', 'no'):
            if item.get('type') == 'boolean':
                # Small models write the expected answer here; for a boolean the reading is derivable.
                evidence_needed = ('A probe that reads '+(creates or source or 'the source')+' and reports the boolean '
                                   +uid+': '+claim.rstrip('.'))
            else:
                refusals.append('unknown '+uid+': evidence_needed must say what reading would settle it, '
                                'not the expected answer'); continue
        if creates and not relative_path(creates):
            refusals.append('unknown '+uid+': creates must be a relative path inside the project'); continue
        # A non-goal names things not to be made (`a framework`, `bundler`, external fonts): an artifact unknown
        # that creates or claims one is refused. Readings are left alone — measuring that a non-goal is
        # respected (external_request_count = 0) is how the map proves it.
        # Matched against what the unknown builds and how it is named, not its claim: a claim that says the report
        # "makes no choice" names the non-goal to say it is respected (logo_mark2, 2026-09-19).
        offending = [term for non_goal in brief.get('non_goals') or [] for term in re.findall(r'`([^`]+)`', str(non_goal))
                     if term and creates and term.lower().replace(' ', '') in creates.lower().replace('_', '').replace('-', '')]
        if offending:
            refusals.append('unknown '+uid+': it builds '+', '.join('`'+t+'`' for t in dict.fromkeys(offending))+', which the brief '
                            'names as a non-goal; a non-goal is respected, not delivered'); continue
        if creates and not source:
            source = creates
        if project is not None and not source and not creates and cites.startswith('deliverable:') and item.get('type') == 'boolean':
            # A boolean about a deliverable-named file that does not exist yet builds it (index_html_built with no
            # `creates` was refused as a reading of a missing file, and its measurements ran unbuilt — docs_page2).
            index = int(cites.split(':')[1])-1
            deliverables = observation['brief'].get('deliverables') or []
            text = deliverables[index] if 0 <= index < len(deliverables) else ''
            for path in re.findall(r'[\w./-]+\.[A-Za-z0-9]+', text):
                if path.lower() in claim.lower() and relative_path(path) and not source_exists(project, path):
                    source = path
                    break
        if project is not None and source and not creates and not source_exists(project, source) and cites.startswith('deliverable:') \
                and relative_path(source) and item.get('type') == 'boolean':
            # An unknown about a deliverable names the file it is about; when that file does not exist yet, the
            # task builds it. Let it through and derive `creates` (in the deliverable's own spelling when it
            # names the file) rather than refusing over a field choice; the worker owns building it.
            index = int(cites.split(':')[1])-1
            deliverables = observation['brief'].get('deliverables') or []
            text = deliverables[index] if 0 <= index < len(deliverables) else ''
            named = [w for w in re.findall(r'[\w./-]+\.[A-Za-z]+', text) if w.lower() == source.lower()]
            creates = source = named[0] if named else source
        # `source` is not a field of an unknown: the unknown is a description (claim + evidence); where a
        # worker looks and what it builds are the route task's business. A source the model still supplies
        # is kept as a hint and never refused.
        unknowns.append(dict(id=uid, claim=claim, evidence_needed=evidence_needed,
                             type=item['type'], quantity=uid, unit=str(item.get('unit') or ''), cites=cites, source=source,
                             creates=creates, also=item.get('also') or [], enabler=item.get('enabler') or '',
                             expression=item.get('expression') or '', vars=item.get('vars') or {},
                             x_quantity=item.get('x_quantity') or '', x_unit=item.get('x_unit') or ''))
    # An artifact is verified by agreement with the map, so its unknown must say which knowns (or
    # unknowns minted alongside) its content agrees with. Without that anchor the probe can only check
    # that the file exists: a report with a table of invented stations passed on 2026-09-18.
    anchors = {k['id'] for k in observation['knowns']} | {u['id'] for u in observation['unknowns']} | {u['id'] for u in unknowns}
    # Source artifacts: files the readings are taken OF (a composition, a dataset the worker writes), not reports
    # that must agree with knowns. One is anchored by the needs that describe it by name or by the unknowns that
    # read it; its measured properties are separate unknowns that depend on the build, and a derived artifact
    # may anchor on it before it exists. Refusing them ("names no known") left the piano benchmark's attempt 2
    # with validators of a MIDI nothing was allowed to write, and the deps guard then made the builder wait on
    # its own readers (2026-09-20).
    source_artifacts: set[str] = set()
    for item in unknowns:   # the normalised list: `creates` is derived from the deliverable's text, the model rarely sends it
        made = str(item.get('creates') or '').lower()
        if not made:
            continue
        others = [u for u in unknowns if u is not item]+list(observation['unknowns'])
        read_by_others = any(made in (str(u.get('source') or '')+' '+str(u.get('claim') or '')).lower() for u in others)
        named_by_needs = any(made in str(n).lower() for n in observation['brief'].get('needs') or [])
        if read_by_others or named_by_needs:
            source_artifacts.add(made)
    for item in list(unknowns):
        # An artifact unknown is one that creates something, or a boolean citing a deliverable ("exits 0" against
        # `source: environment` is the existence check by another door). A number or label citing a deliverable
        # is a reading OF the artifact — contrast, line length — and needs no anchor; refusing those threw away
        # the design unknowns the controller had pulled from the library (docs_page, 2026-09-19).
        artifact = (item['creates'] or (item['cites'].startswith('deliverable:') and item.get('type') == 'boolean')) \
            and not item.get('enabler')
        if artifact and item.get('type') == 'label':
            # An artifact is verified by agreement with the map, never by recording what it prints.
            cautions.append('unknown '+item['id']+': an artifact unknown is usually an agreement (boolean) or a value (number), '
                            'not a label; applied as minted')
        # A statement about what the map does NOT hold ("the report lists the questions the data could not
        # answer") is anchored on the proposals that record it (CR-001), not on a known.
        proposal_ids = {str(p.get('id')) for p in observation['brief'].get('proposals') or []}
        names_proposal = any(m in proposal_ids for m in re.findall(r'CR-\d+', item['claim']+' '+item['evidence_needed']))
        # A built thing (a page, a script) is anchored on the source it is built from: an existing project file the
        # deliverable's text names (content/pitch.md). Its measured properties are separate unknowns taken after the
        # build; anchoring the build on readings of itself deadlocked landing-en (2026-09-19).
        text = item['claim']+' '+item['evidence_needed']
        deliverable_text = ''
        if item['cites'].startswith('deliverable:'):
            entries = observation['brief'].get('deliverables') or []
            index = int(item['cites'].split(':')[1])-1
            deliverable_text = entries[index] if 0 <= index < len(entries) else ''
        named_files = [f for f in re.findall(r'[\w./-]+\.[A-Za-z0-9]+', deliverable_text+' '+text)
                       if f.lower() != str(item['creates']).lower() and f in text and project is not None and source_exists(project, f)]
        made = str(item['creates'] or '').lower()
        # A derived artifact (notes.md about piece.mid) anchors on a source artifact this same briefing mints.
        named_files += [f for f in re.findall(r'[\w./-]+\.[A-Za-z0-9]+', text) if f.lower() in source_artifacts and f.lower() != made]
        if artifact and not names_proposal and not named_files and made not in source_artifacts \
                and not [w for w in re.findall(r'[a-z][a-z0-9_]*', text) if w in anchors and w != item['id']]:
            cautions.append('unknown '+item['id']+': it is about '+(item['creates'] or item['cites'])+' but names no known '
                            'or unknown its content must agree with; an artifact is verified against the map — name '
                            'them in the evidence ("the STN01 row matches stn01_mean_temp_c", "the tests assert '
                            'station_count and mean_temp_c"), minting number unknowns first when the map lacks them; '
                            'a statement that a need cannot be answered is anchored on the open proposal that records '
                            'it (name its id, e.g. CR-001); a thing that is built (a page, a script) is anchored on the '
                            'source files it is built from (name them: "sections follow content/pitch.md"), and its '
                            'measured properties are separate unknowns that depend on the build')
    minted = {u['id'] for u in unknowns}
    for index, line in enumerate(refusals):
        m = re.match(r'unknown ([a-z][a-z0-9_]*): ', line)
        if m:
            refused_unknowns.setdefault(m.group(1), index)
    for item in decision.get('tasks') or []:
        if not isinstance(item, dict):
            refusals.append('task entry is not an object'); continue
        tid = str(item.get('id') or '')
        listed = item.get('unknowns')
        if not listed and item.get('unknown'):
            listed = [item.get('unknown')]
        alias = {new: orig for orig, new in reopened.items()}
        ids = [alias.get(str(u), str(u)) for u in (listed or []) if str(u) not in dropped_duplicates]
        if not ID_PATTERN.match(tid):
            refusals.append('task '+repr(tid)+': id must match ^[a-z][a-z0-9_]*$'); continue
        if tid in existing_tasks or any(t['id'] == tid for t in tasks):
            noted.append('task '+tid+': already on the route'); continue
        if listed and not ids:
            noted.append('task '+tid+': every reading it names is already on the map and current ('
                         +', '.join(dropped_duplicates[str(u)] for u in listed if str(u) in dropped_duplicates)+'); not routed'); continue
        if not ids or len(set(ids)) != len(ids):
            refusals.append('task '+tid+': list the unknowns it resolves (one or more, no repeats)'); continue
        stale_ids = {k['id'] for k in observation['knowns'] if k.get('stale')}
        # A false reading about an artifact is routed again under its own id: the task changes the artifact and
        # re-takes the reading. Without this the false could neither be re-minted (one claim, one reading) nor
        # routed (only stale ids were), and social_card's controller could only propose (2026-09-19).
        # The rule covers every false reading whose subject is a project file, not only ones that cite a deliverable:
        # logo_mark7's "every candidate has mark-mono.svg using a single fill" cited need 6, read false, and the
        # controller was refused a repair task nine evals running until it stalled (2026-09-20).
        by_unknown = {u['id']: u for u in observation['unknowns']}
        brief_entries = {'need': observation['brief'].get('needs') or [], 'deliverable': observation['brief'].get('deliverables') or []}

        def about_a_file(known_id: str) -> bool:
            u = by_unknown.get(known_id) or {}
            notes = str(u.get('notes') or '')
            if 'creates ' in notes or 'cites deliverable:' in notes:
                return True
            text = str(u.get('claim') or '')
            # The reading's source is the file it is about, whatever the claim's words (the nocturne gym's
            # left_hand_wide_rolling_compound reads piece.mid and says "the left hand", 2026-09-20).
            source = re.search(r'source ([\w./-]+)', notes)
            if source:
                text += ' '+source.group(1)
            for kind, index in re.findall(r'cites (need|deliverable):(\d+)', notes):
                entries = brief_entries[kind]
                text += ' '+(entries[int(index)-1] if 0 < int(index) <= len(entries) else '')
            named = re.findall(r'[\w./-]+\.[A-Za-z0-9]+', text)
            if project is not None and any(source_exists(project, f) or any(True for _ in project.glob('**/'+f)) for f in named):
                return True
            # Nothing named: the reading is about what the project makes when the project makes anything (the
            # benchmark's phrase_structure_valid — "phrases of 4 or 8 bars" — is about piece.mid without saying so,
            # 2026-09-20). Only a reading whose named source is a file no task creates is about given data.
            made = {str(unknown_created(x)).lower() for x in observation['unknowns'] if unknown_created(x)}
            if named and not any(f.lower() in made for f in named):
                return False
            return bool(made)
        # A boolean that reads false about a file the project makes is a reading the file must be changed to pass,
        # and a task on its own id is the repair: the reading is reopened and taken again after. (A false reading
        # about given data is the brief being wrong — a proposal, not a task — and stays refused.)
        false_artifacts = {k['id'] for k in observation['knowns'] if k['type'] == 'boolean' and k.get('rate') is not None
                           and float(k['rate']) < 0.5 and about_a_file(k['id'])}
        for u in ids:
            if u in false_artifacts and u not in open_unknowns and u not in minted and u not in reopened:
                reopened[u] = u
                noted.append('unknown '+u+': reads false; reopened for the repair task '+tid+' to take again')
        bad = [u for u in ids if u not in minted and u not in open_unknowns and u not in stale_ids
               and u not in reopened]
        if bad:
            kept = [u for u in ids if u not in bad]
            if not kept:
                roots = [refused_unknowns[u] for u in bad if u in refused_unknowns]
                if len(roots) == len(bad):
                    # Every unknown it carried was refused above: the task goes with them, said once.
                    for index in sorted(set(roots)):
                        refusals[index] += ' — task '+tid+' goes with it'
                    continue
                refusals.append('task '+tid+': unknown '+', '.join(repr(u) for u in bad)+' is neither minted here nor open'); continue
            # A task carrying several unknowns keeps the ones that passed; the refused ones were reported above.
            cautions.append('task '+tid+': dropped unknown '+', '.join(repr(u) for u in bad)+' (refused or absent); kept '+', '.join(kept))
            ids = kept
        taken = [u for u in ids if u in routed or any(u in t['unknowns'] for t in tasks)]
        if taken:
            kept = [u for u in ids if u not in taken]
            if not kept:
                refusals.append('task '+tid+': unknown '+', '.join(taken)+' already has an open task'); continue
            # One unknown already routed does not sink the others the task carries.
            cautions.append('task '+tid+': dropped unknown '+', '.join(taken)+' (already has an open task); kept '+', '.join(kept))
            ids = kept
        if item.get('bucket') not in BUCKETS:
            refusals.append('task '+tid+': bucket must be one of '+', '.join(BUCKETS)); continue
        deps = [str(d) for d in item.get('deps') or []]
        # A dependency may name a task later in the same reply; apply() adds tasks in dependency order. A task
        # refused above is gone from `tasks` and is dropped below once the reply is done (see the covered check).
        later = {str(ti.get('id')) for ti in (decision.get('tasks') or []) if isinstance(ti, dict)}
        bad = [d for d in deps if d not in existing_tasks and not any(t['id'] == d for t in tasks) and d not in later]
        if bad:
            # A dependency on a task refused above (or never named) is dropped, not fatal: one bad task
            # otherwise sinks every task behind it and every unknown they carried (components, 2026-09-19).
            cautions.append('task '+tid+': dropped dependency '+', '.join(bad)+' (refused or absent); kept the task')
            deps = [d for d in deps if d not in bad]
        title = str(item.get('title') or '').strip()
        if not title:
            refusals.append('task '+tid+': title is required'); continue
        # An enabler is built before the readings, not after them: it is not an artifact that agrees with the map.
        artifact_ids = {u['id'] for u in unknowns if u.get('creates') and not u.get('enabler')} | {
            u['id'] for u in observation['unknowns'] if 'creates ' in str(u.get('notes') or '') and '; enabler ' not in str(u.get('notes') or '')}
        builds = any(u in artifact_ids for u in ids)
        # A reading of a file some task builds waits for that task: the static audit of site/index.html was
        # routed beside the page build and blocked on an absent file (landing-en, 2026-09-19).
        creators: dict[str, str] = {}
        decided_tasks = [ti for ti in (decision.get('tasks') or []) if isinstance(ti, dict)]
        for u in unknowns:
            if u.get('creates') and not u.get('enabler'):
                # The builder may come later in the same reply; apply() adds tasks in dependency order.
                creators[u['creates'].lower()] = next((str(ti.get('id')) for ti in decided_tasks
                                                       if u['id'] in (ti.get('unknowns') or ([ti['unknown']] if ti.get('unknown') else []))), '')
        for u in observation['unknowns']:
            notes = str(u.get('notes') or '')
            if 'creates ' in notes and '; enabler ' not in notes:
                made = notes.split('creates ', 1)[1].split(';')[0].strip().lower()
                owner_task = next((t['id'] for t in observation['tasks'] if u['id'] in (t.get('unknowns') or [])
                                   and t['status'] in OPEN_TASK), '')
                creators.setdefault(made, owner_task)
        def names_file(text: str, made: str) -> bool:
            # `brand/marks/x/mark.svg` is named by "mark.svg", by "brand/marks", or by the bare stem "mark" as a word
            # ("render each candidate mark") — the stem match applies only while the file does not exist yet.
            low = made.lower()
            stem = low.rsplit('/', 1)[-1].rsplit('.', 1)[0]
            return low in text or (len(stem) > 2 and re.search(r'\b'+re.escape(stem)+r's?\b', text) is not None)
        if builds:
            # A builder never waits on a reader of what it builds (build_candidate_mark_assets was made to depend on
            # render_marks_small — logo_mark6); the reverse dependency is the true one and is added below.
            made_by_me = [u['creates'].lower() for u in unknowns if u['id'] in ids and u.get('creates')]
            for d in list(deps):
                reader = next((ti for ti in decided_tasks if str(ti.get('id')) == d), None)
                if reader is None:
                    continue
                r_ids = reader.get('unknowns') or ([reader['unknown']] if reader.get('unknown') else [])
                r_text = ' '.join(str(u.get('source') or '')+' '+str(u.get('claim') or '') for u in unknowns if u['id'] in r_ids).lower()
                r_builds = any(u.get('creates') for u in unknowns if u['id'] in r_ids)
                if not r_builds and any(names_file(r_text, m) for m in made_by_me):
                    deps.remove(d)
                    cautions.append('task '+tid+': dropped dependency '+d+' — it reads what this task builds; the reverse holds')
        if not builds:
            mine = ' '.join(str(u.get('source') or '')+' '+str(u.get('claim') or '') for u in unknowns if u['id'] in ids).lower()
            for made, owner_task in creators.items():
                exists = project is not None and (project/made).exists()
                if owner_task and owner_task != tid and made and (made in mine or (not exists and names_file(mine, made))) \
                        and owner_task not in deps:
                    deps.append(owner_task)
                    cautions.append('task '+tid+': reads '+made+', which '+owner_task+' builds — added that dependency')
            # A reading of a path a deliverable names that does not exist yet, with nothing routed to build it, is
            # a task that can only block ("count the candidates under brand/marks/" before any were drawn). Checked
            # whatever the task already depends on: inspect_mark_files waited on the mark builder and still read
            # mark-mono.svg and wordmark.svg, which nothing built (logo_mark8).
            if project is not None:
                named = {p.rstrip('/').lower() for text in (observation['brief'].get('deliverables') or [])
                         for p in re.findall(r'[\w./-]+/[\w./-]*|[\w./-]+\.[A-Za-z0-9]+', str(text))}
                named = {p.split('<')[0].rstrip('/') for p in named if p.split('<')[0].rstrip('/')}
                def exists_somewhere(p: str) -> bool:
                    return (project/p).exists() or ('/' not in p and any(project.rglob(p)))
                missing = sorted({p for p in named if p and (p in mine or ('/' not in p and re.search(r'\b'+re.escape(p)+r'\b', mine)))
                                  and not exists_somewhere(p) and not any(p in c for c in creators)})
                if missing:
                    cautions.append('task '+tid+': reads '+', '.join(missing[:3])+', which does not exist and no task builds — '
                                    'mint the artifact unknown that creates it (with `creates`) and its task first, and make '
                                    'this task depend on it')
        made_here = {u['creates'].lower() for u in unknowns if u['id'] in ids and u.get('creates')}
        def reads_own(task_unknowns: list[str]) -> bool:
            texts = ' '.join(str(u.get('source') or '')+' '+str(u.get('claim') or '') for u in unknowns if u['id'] in task_unknowns)
            texts += ' '.join(str(u.get('claim') or '')+' '+str(u.get('notes') or '') for u in observation['unknowns'] if u['id'] in task_unknowns)
            return any(m and m in texts.lower() for m in made_here)
        reading_tasks = [t['id'] for t in tasks if not any(u in artifact_ids for u in t['unknowns']) and not reads_own(t['unknowns'])]
        reading_tasks += [t['id'] for t in observation['tasks'] if t['status'] in OPEN_TASK
                          and not any(u in artifact_ids for u in (t.get('unknowns') or [])) and not reads_own(t.get('unknowns') or [])]
        builds_source = any(str(u.get('creates') or '').lower() in source_artifacts for u in unknowns if u['id'] in ids and u.get('creates'))
        if builds and not deps and reading_tasks and not builds_source:
            # An artifact that must agree with the map cannot be built before the readings exist. A source
            # artifact is the other way round: its readers depend on it (added above), it depends on nobody.
            cautions.append('task '+tid+': it builds an artifact that must agree with the map; it depends on the '
                            'tasks that produce those knowns; add deps from: '+', '.join(dict.fromkeys(reading_tasks))+' — applied as routed; add the deps if you meant them')
        carried = {u['enabler'] for u in unknowns if u['id'] in ids and u.get('enabler')}
        if len(carried) > 1:
            refusals.append('task '+tid+': one enabler per task ('+', '.join(sorted(carried))+')'); continue
        walk = str(item.get('walk') or '').strip()
        walk_from = int(item.get('walk_from') or 0) if str(item.get('walk_from') or '').isdigit() or isinstance(item.get('walk_from'), int) else 0
        if walk:
            known_methods = {m['id'] for m in observation.get('methods') or []}
            if walk not in known_methods and not _procedure_exists(walk, observation.get('playbook_store') or ''):
                cautions.append('task '+tid+': walk '+repr(walk)+' is not a procedure in the playbook; the worker searches instead')
                walk, walk_from = '', 0
        if walk:
            by_method = {m['id']: m for m in observation.get('methods') or []}
            picked = by_method.get(walk)
            if picked is not None and int(picked.get('gravity') or 0) == 0:
                heavier = [m for m in by_method.values() if int(m.get('gravity') or 0) >= 2 and m['id'] != walk]
                if heavier:
                    cautions.append('task '+tid+': walk '+repr(walk)+' has gravity 0 — a method one gym wrote for its own piece — while '
                                    +', '.join('`'+m['id']+'` (gravity '+str(m.get('gravity'))+')' for m in heavier[:3])
                                    +' is near this brief; a leaf is right when its specifics are the task, otherwise name the method the library leans on')
        if walk and walk_from:
            # The next walk of a long method follows a first one: on the route already, in this decision, or left
            # open on a workspace. Routed alone it starts a worker in the middle of a method (attempt 4 routed
            # render_piece_mp3_part2 @50 and no part 1).
            earlier = any(('walk:'+walk+'@') in ' '.join(str(a) for a in (t.get('acceptance') or [])) for t in observation['tasks']) \
                or any(t.get('walk') == walk and int(t.get('walk_from') or 0) < walk_from for t in tasks) \
                or any(str(x.get('procedure')) == walk for w in observation.get('workspaces') or [] for x in w.get('walks_open') or [])
            if not earlier:
                cautions.append('task '+tid+': walk_from '+str(walk_from)+' with no earlier walk of '+repr(walk)+' on the route or a '
                                'workspace; it starts from 0 — the next walk is routed when the first leaves it')
                walk_from = 0
        tasks.append(dict(id=tid, title=title, unknowns=ids, unknown=ids[0], bucket=item['bucket'], deps=deps,
                          enabler=next(iter(carried), ''), walk=walk, walk_from=walk_from))
    accepted_ids = existing_tasks | {t['id'] for t in tasks}
    for t in tasks:
        gone = [d for d in t['deps'] if d not in accepted_ids]
        if gone:
            cautions.append('task '+t['id']+': dropped dependency '+', '.join(gone)+' (refused); kept the task')
            t['deps'] = [d for d in t['deps'] if d in accepted_ids]
    tasks = _merge_sibling_tasks(tasks, cautions)
    covered = {u for t in tasks for u in t['unknowns']}
    listed_by_refused = {str(u) for ti in (decision.get('tasks') or []) if isinstance(ti, dict)
                         and not any(t['id'] == str(ti.get('id')) for t in tasks)
                         for u in (ti.get('unknowns') or ([ti['unknown']] if ti.get('unknown') else []))}
    for uid in minted - covered:
        if uid in listed_by_refused:
            # Its task was refused above and that line stands for both; the unknown is dropped with it.
            continue
        refusals.append('unknown '+uid+': minted without a task; every unknown is routed by exactly one task')
    unknowns = [u for u in unknowns if u['id'] in covered]
    open_summaries = {str(p.get('summary') or '').split(' \u2014 evidence:')[0].strip().lower()
                      for p in observation['brief'].get('proposals') or []}
    for item in decision.get('proposals') or []:
        if not isinstance(item, dict) or not str(item.get('summary') or '').strip():
            refusals.append('proposal without a summary'); continue
        label = 'proposal '+repr(item.get('summary'))[:60]
        if not str(item.get('evidence') or '').strip():
            refusals.append(label+': evidence is required'); continue
        fields = {k: str(item[k]).strip() for k in ('need', 'deliverable', 'non_goal', 'mission') if str(item.get(k) or '').strip()}
        # A budget ask is its own patch — the points, nothing else. Every budget CR today was smuggled into a
        # need ("provide five more points"), which an acceptance then wrote into the brief as a requirement
        # while the budget stayed where it was (attempt 3, 2026-09-20).
        if item.get('budget_points') is not None and item.get('budget_delta') is None:
            refusals.append(label+': the budget is never written as a number; ask for more with "budget_delta": +N (the '
                            'points beyond the current '+str(observation['brief'].get('budget_points'))+')'); continue
        if item.get('budget_delta') is not None:
            # A budget ask is a delta, on its own: added to the budget as it stands when the person accepts. The
            # changing-meter gym's CR-001 carried `budget_points: 3` (the price of the task it added) beside a need
            # and a deliverable, was accepted for those, and cut a 120-point budget to 3 (2026-09-21).
            try:
                delta = int(item['budget_delta'])
            except (TypeError, ValueError):
                refusals.append(label+': budget_delta is a whole number of points, +N'); continue
            if delta <= 0:
                refusals.append(label+': budget_delta asks for more points (+N); giving points back is the person\'s call, not yours'); continue
            if observation['brief'].get('budget_points') is None:
                refusals.append(label+': the brief has no budget to add to'); continue
            if fields:
                refusals.append(label+': a budget change is budget_delta alone; carry the need, deliverable or non-goal in a proposal of its own'); continue
            fields['budget_delta'] = '+'+str(delta)
        if any(re.search(r'\b(points?|budget)\b', v, re.I) for k, v in fields.items() if k != 'budget_delta'):
            refusals.append(label+': points are asked for with budget_delta, never as a need or deliverable about points'); continue
        if any(re.fullmatch(r'(need|deliverable):?\s*\d+', v) for v in fields.values()):
            refusals.append(label+': "need"/"deliverable" add an entry and carry its new text; to change or drop an '
                            'existing one use "edit": {"need": N, "text": "..."} or "remove": {"need": N}'); continue
        # A proposal can do anything a person can do to the brief: rewrite an entry in place or remove it.
        # Indices are 1-based as the brief is rendered; the guard checks they exist before terra sees them.
        counts = {k: len(observation['brief'].get(k+'s' if k != 'non_goal' else 'non_goals') or []) for k in ('need', 'deliverable', 'non_goal')}
        bad = None
        for verb in ('edit', 'remove'):
            spec = item.get(verb)
            if spec is None:
                continue
            if not isinstance(spec, dict) or len([k for k in spec if k in counts]) != 1:
                bad = label+': "'+verb+'" names exactly one of need, deliverable, non_goal with its number'; break
            kind = next(k for k in spec if k in counts)
            try:
                index = int(spec[kind])
            except (TypeError, ValueError):
                bad = label+': "'+verb+'" '+kind+' must be its number'; break
            if not 1 <= index <= counts[kind]:
                bad = label+': '+kind+' '+str(index)+' does not exist (the brief has '+str(counts[kind])+')'; break
            if verb == 'edit':
                text = str(spec.get('text') or '').strip()
                if not text:
                    bad = label+': "edit" carries the new text'; break
                fields['edit_'+kind] = str(index)+': '+text
            else:
                fields['remove_'+kind] = str(index)
        if bad:
            refusals.append(bad); continue
        if not fields:
            refusals.append(label+': changes nothing (need, deliverable, non_goal, mission, edit, remove or budget_delta)'); continue
        if str(item['summary']).strip().lower() in open_summaries:
            refusals.append(label+': an open proposal already says this; wait for the person to decide'); continue
        rejected = [p for p in observation['brief'].get('decided') or [] if p.get('status') == 'rejected'
                    and _same_reading(str(p.get('summary') or '').split(' \u2014 evidence:')[0], str(item['summary']))]
        if rejected:
            refusals.append(label+': the person rejected this ('+str(rejected[0].get('id'))
                            +(': '+str(rejected[0]['decision_reason'])[:120] if rejected[0].get('decision_reason') else '')
                            +'); it is not proposed again — work within the brief as it stands'); continue
        # A proposal says whether the work can go on around it. Blocking: the brief's flaw makes the rest of the
        # work meaningless until a person decides, and the loop stops now. Otherwise the loop finishes what it
        # can and ends proposals_pending — a project never wraps up as met while a proposal is open.
        blocking = item.get('blocking', False)
        if not isinstance(blocking, bool):
            refusals.append(label+': "blocking" is true or false (omit it when the rest of the work can go on)'); continue
        proposals.append(dict(summary=str(item['summary']).strip(), evidence=str(item['evidence']).strip(), blocking=blocking, **fields))
        open_summaries.add(str(item['summary']).strip().lower())
    by_id = {t['id']: t for t in observation['tasks']}
    for item in decision.get('rebucket') or []:
        if not isinstance(item, dict):
            refusals.append('rebucket entry is not an object'); continue
        tid, bucket = str(item.get('task') or ''), item.get('bucket')
        current = by_id.get(tid)
        if current is None:
            refusals.append('rebucket '+repr(tid)+': no such task'); continue
        if current['status'] != 'blocked' or not str(current.get('blocked_reason') or '').startswith(BUDGET_BLOCK):
            refusals.append('rebucket '+tid+': only a task blocked on budget can be re-bucketed'); continue
        if bucket not in BUCKETS or BUCKETS.index(bucket) <= BUCKETS.index(current['bucket'] or 'low'):
            refusals.append('rebucket '+tid+': bucket must be above '+str(current['bucket'])); continue
        rebucket.append(dict(task=tid, bucket=bucket, why=str(item.get('why') or '')))
    cancel: list[dict[str, str]] = []
    for item in decision.get('cancel') or []:
        if not isinstance(item, dict):
            refusals.append('cancel entry is not an object'); continue
        tid, why = str(item.get('task') or ''), str(item.get('why') or '').strip()
        current = by_id.get(tid)
        if current is None:
            refusals.append('cancel '+repr(tid)+': no such task'); continue
        if current['status'] not in ('ready', 'blocked'):
            refusals.append('cancel '+tid+': only a ready or blocked task comes off the route ('+str(current['status'])+')'); continue
        if not why:
            refusals.append('cancel '+tid+': say why'); continue
        cancel.append(dict(task=tid, why=why))
    reopen: list[dict[str, str]] = []
    for item in decision.get('reopen') or []:
        # A done work order owed again: its known went stale or the gate names its reading. The same work order
        # reopens and its worker picks up where it left off — never a new one continuing it.
        if not isinstance(item, dict):
            refusals.append('reopen entry is not an object'); continue
        tid, why = str(item.get('task') or ''), str(item.get('why') or '').strip()
        current = by_id.get(tid)
        if current is None:
            refusals.append('reopen '+repr(tid)+': no such task'); continue
        if current['status'] != 'done':
            refusals.append('reopen '+tid+': only a done work order reopens ('+str(current['status'])+'; a blocked one is unblocked)'); continue
        if not why:
            refusals.append('reopen '+tid+': say what no longer stands'); continue
        reopen.append(dict(task=tid, why=why))
    for item in decision.get('unblock') or []:
        if not isinstance(item, dict):
            refusals.append('unblock entry is not an object'); continue
        tid, after = str(item.get('task') or ''), str(item.get('after') or '')
        current = by_id.get(tid)
        if current is None:
            refusals.append('unblock '+repr(tid)+': no such task'); continue
        if current['status'] != 'blocked' or str(current.get('blocked_reason') or '').startswith(BUDGET_BLOCK):
            refusals.append('unblock '+tid+': only a task its worker blocked can be released (budget blocks are re-bucketed)'); continue
        waited = by_id.get(after)
        if waited is None or waited['status'] != 'done':
            # A release needs a reason the map can check: the task whose completion changed the source.
            refusals.append('unblock '+tid+': "after" must name a task that has since completed (the one that built what was missing)'); continue
        # Released once on this evidence and blocked again: the reason stands. Ten releases of the same task after
        # the same done task ran a worker in a circle (logo_mark, 2026-09-19).
        if (tid, after) in released_before(project):
            refusals.append('unblock '+tid+': it was already released after '+after+' and blocked again, so that reason stands — '
                            'mint the readings its block names as unknowns, propose the change, or leave it'); continue
        unblock.append(dict(task=tid, after=after))
    for item in decision.get('retype') or []:
        # The worker measured a list where a number was asked, or a name where a number was: the question was
        # typed wrong, and the fix is the same unknown asked with the right type (and, if it must, a sharper
        # claim), not a second unknown and not a proposal. Only an unresolved unknown may be retyped.
        if not isinstance(item, dict):
            refusals.append('retype entry is not an object'); continue
        uid, new_type = str(item.get('unknown') or ''), item.get('type')
        current = existing_unknowns.get(uid)
        if current is None:
            refusals.append('retype '+repr(uid)+': no such unknown'); continue
        if current.get('status') == 'resolved':
            refusals.append('retype '+uid+': it is resolved; a resolved reading is re-taken by routing its id, not retyped'); continue
        if new_type not in TYPES:
            refusals.append('retype '+uid+': type must be one of '+', '.join(TYPES)); continue
        claim = str(item.get('claim') or '').strip()
        if new_type == current.get('type') and not claim:
            refusals.append('retype '+uid+': same type and no new claim changes nothing'); continue
        retype.append(dict(unknown=uid, type=new_type, claim=claim, why=str(item.get('why') or '')))
    done = decision.get('done')
    done = bool(done) if isinstance(done, bool) else None
    if require_deliverables:
        # A route step that leaves a deliverable with no unknown has routed the readings and forgotten the thing
        # they read: thirteen measurements of marks nobody was asked to draw (logo_mark4, 2026-09-19). The reply is
        # sent back for the builder — boolean, `creates`, anchored on the source it is built from.
        cited = {ref for u in observation['unknowns'] for ref in re.findall(r'(?:need|deliverable):\d+', str(u.get('notes') or ''))}
        cited |= {u['cites'] for u in unknowns} | {a for u in unknowns for a in (u.get('also') or [])}
        for index, text in enumerate(observation['brief'].get('deliverables') or [], start=1):
            ref = 'deliverable:'+str(index)
            if ref in cited or phases.refused_cites(observation['brief'], [ref]):
                continue
            cautions.append(ref+' has no unknown yet: the readings you routed are of what it names, and nothing builds it. Mint '
                            'its artifact unknown first (boolean, `creates` the file, claim naming the source it is built '
                            'from) with a task; the readings depend on that task')
    if done is True and (observation['brief'].get('proposals') or proposals):
        done = False
        refusals.append('done refused: a proposal is open — the brief is not met until a person decides it; '
                        'route what can still be measured, or leave it pending')
    if done is True:
        uncovered = uncovered_deliverable_terms(observation, unknowns, open_phases_only=True)
        if uncovered:
            done = False
            refusals.append('done refused: '+'; '.join(uncovered)+' — mint one unknown per named thing (with `creates`), '
                            'each claim naming it and the known it must agree with')
    return dict(unknowns=unknowns, tasks=tasks, proposals=proposals, rebucket=rebucket, unblock=unblock, retype=retype, noted=noted,
                cancel=cancel, reopen_unknowns=sorted(reopened), reopen=reopen, cautions=cautions, done=done, why=str(decision.get('why') or '')), refusals


def apply(config: dict[str, Any], project: Path, accepted: dict[str, Any], root: Path | None = None) -> dict[str, list[str]]:
    """Write the accepted decision through Terra; proposals are queued, never accepted here."""
    done = dict(unknowns=[], tasks=[], proposals=[], rebucket=[], unblock=[], retype=[], cancel=[])
    for uid in accepted.get('reopen_unknowns') or []:
        # A resolved reading to be taken again: open on the map, so its task is routable and its known is replaced.
        try:
            terra(config, project, 'unknown', 'status', uid, 'open', '--notes', 'reopened by the controller: measure again after the artifact changed')
            done.setdefault('reopen', []).append(uid)
        except RuntimeError as error:
            done.setdefault('refused', []).append(uid+': reopen: '+str(error)[:200])
    for u in accepted['unknowns']:
        args = ['unknown', 'create', u['id'], '--claim', u['claim'], '--evidence', u['evidence_needed'],
                '--type', u['type'], '--quantity', u['quantity'],
                '--notes', 'cites '+u['cites']+('; also '+', '.join(u['also']) if u.get('also') else '')
                +('; source '+u['source'] if u.get('source') else '')
                +('; creates '+u['creates'] if u.get('creates') else '')
                +('; enabler '+u['enabler'] if u.get('enabler') else '')]
        if u['unit']:
            args += ['--unit', u['unit']]
        if u['type'] == 'formula':
            args += ['--expression', u['expression']]
            for name, bound in (u.get('vars') or {}).items():
                args += ['--var', name+'='+bound]
        if u['type'] == 'relation':
            args += ['--x-quantity', str(u.get('x_quantity'))]+(['--x-unit', str(u['x_unit'])] if u.get('x_unit') else [])
        terra(config, project, *args)
        done['unknowns'].append(u['id'])
    for r in accepted.get('cancel') or []:
        # Before the tasks, so a replacement routes onto the unknown the cancelled task held. Off the route: superseded, wrong, or its unknown is now carried by another task. Terra strands the
        # task's dependents until re-pointed; the eval sees them on the next briefing.
        try:
            terra(config, project, 'route', 'cancel', r['task'], '--reason', r['why'])
            done.setdefault('cancel', []).append(r['task'])
        except RuntimeError as error:
            done.setdefault('refused', []).append('cancel '+r['task']+': '+str(error)[:200])
    now = phases.current(terra(config, project, 'brief', 'show')) if accepted['tasks'] else None
    sectors = set()
    if now and (project/layout.dirname(project)/'route.json').exists():
        sectors = {s.get('id') for s in json.loads((project/layout.dirname(project)/'route.json').read_text()).get('sectors') or []}
    ordered: list[dict[str, Any]] = []
    pending = list(accepted['tasks'])
    while pending:   # dependency order: a task follows the tasks it depends on within this reply
        ready = [t for t in pending if all(d not in {p['id'] for p in pending} for d in t.get('deps') or [])]
        ordered += ready or pending[:1]
        pending = [t for t in pending if t not in ordered]
    for t in ordered:
        ids = t.get('unknowns') or [t['unknown']]
        args = ['route', 'add', t['id'], '--title', t['title'], '--map', ids[0], '--bucket', t['bucket'],
                '--skill', 'tooling' if t.get('enabler') else 'terra-probe']
        if t.get('enabler'):
            args += ['--role', 'enabler', '--enabler', t['enabler']]
        if now:
            args += ['--phase', now['id']]
            if now['id'] in sectors:
                args += ['--sector', now['id']]   # the phase's provision: its points, not the next phase's
        for extra in ids[1:]:
            args += ['--accept', 'unknown:'+extra]  # the task resolves these too; Terra's map_id holds only one
        if t.get('walk'):
            args += ['--accept', 'walk:'+t['walk']+'@'+str(int(t.get('walk_from') or 0))]   # the procedure walk this task opens
        for dep in t['deps']:
            args += ['--dep', dep]
        try:
            terra(config, project, *args)
        except RuntimeError as error:
            # Terra refused (usually the plan exceeding budget_points): the refusal is state, not a crash.
            done.setdefault('refused', []).append(t['id']+': '+str(error)[:300])
            continue
        done['tasks'].append(t['id'])
        if t.get('enabler'):
            terra(config, project, 'brief', 'enabler', t['enabler'], 'building')
    for p in accepted['proposals']:
        args = ['brief', 'propose', '--summary', ('[blocking] ' if p.get('blocking') else '')+p['summary']+' — evidence: '+p['evidence']]
        for key in ('need', 'deliverable', 'non_goal', 'mission',
                    'edit_need', 'edit_deliverable', 'edit_non_goal', 'remove_need', 'remove_deliverable', 'remove_non_goal',
                    'budget_delta'):
            if p.get(key):
                args += ['--'+key.replace('_', '-')+('='+p[key] if key == 'budget_delta' else ''), *([] if key == 'budget_delta' else [p[key]])]
        terra(config, project, *args)
        done['proposals'].append(p['summary'])
    for r in accepted.get('rebucket') or []:
        terra(config, project, 'route', 'set-effort', r['task'], '--bucket', r['bucket'])
        terra(config, project, 'route', 'unblock', r['task'])
        done['rebucket'].append(r['task']+'→'+r['bucket'])
    for r in accepted.get('reopen') or []:
        try:
            terra(config, project, 'route', 'reopen', r['task'], '--reason', r['why'])
            from .worker import task_unknown_ids
            task = next((t for t in terra(config, project, 'route', 'status')['tasks'] if t['id'] == r['task']), {})
            for uid in task_unknown_ids(task):
                terra(config, project, 'unknown', 'status', uid, 'open', '--notes', 'reopened: '+r['why'][:300])
            # The reason reaches the worker when its session reopens (worker.py delivers <task root>/reopen.md).
            if root is not None:
                (root/'tasks'/r['task']).mkdir(parents=True, exist_ok=True)
                (root/'tasks'/r['task']/'reopen.md').write_text(r['why'])
            done.setdefault('reopen', []).append(r['task'])
        except RuntimeError as error:
            done.setdefault('refused', []).append('reopen '+r['task']+': '+str(error)[:200])
    for r in accepted.get('unblock') or []:
        terra(config, project, 'route', 'unblock', r['task'])
        record_release(project, r['task'], r['after'])
        done.setdefault('unblock', []).append(r['task']+' after '+r['after'])
    for r in accepted.get('retype') or []:
        path = layout.map_root(project)/'unknowns'/(r['unknown']+'.json')
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        notes = re.sub(r';?\s*blocked: .*$', '', str(record.get('notes') or '')).strip()
        terra(config, project, 'unknown', 'delete', r['unknown'])
        args = ['unknown', 'create', r['unknown'], '--claim', r['claim'] or str(record.get('claim') or ''),
                '--evidence', str(record.get('evidence_needed') or ''), '--type', r['type'], '--quantity', r['unknown'], '--notes', notes]
        if record.get('unit'):
            args += ['--unit', str(record['unit'])]
        terra(config, project, *args)
        # The task that carries it, if its worker blocked on the wrong type, is released to try again.
        route = json.loads((project/layout.dirname(project)/'route.json').read_text())
        for t in route.get('tasks') or []:
            carried = [t.get('map_id')]+[a.removeprefix('unknown:') for a in t.get('acceptance') or [] if str(a).startswith('unknown:')]
            if r['unknown'] in carried and t.get('status') == 'blocked':
                try:
                    terra(config, project, 'route', 'unblock', t['id'])
                except RuntimeError:
                    pass
        done.setdefault('retype', []).append(r['unknown']+'→'+r['type'])
    return done


def released_before(project: Path | None) -> set[tuple[str, str]]:
    """(task, after) pairs the loop has already released, from the project's own record."""
    if project is None:
        return set()
    path = project/layout.dirname(project)/'.mizpah-unblocks.json'
    try:
        return {tuple(p) for p in json.loads(path.read_text())}
    except (OSError, ValueError):
        return set()


def record_release(project: Path, task: str, after: str) -> None:
    path = project/layout.dirname(project)/'.mizpah-unblocks.json'
    pairs = released_before(project) | {(task, after)}
    path.write_text(json.dumps(sorted(pairs))+'\n')


def close_ready_phase(config: dict[str, Any], project: Path) -> dict[str, Any] | None:
    """Close the current phase when everything it owns is resolved and covered; returns what closed, or None.

    Mechanical, like the gate: the controller's judgment inside a phase is "done" for the phase, and the
    loop moves on only when the map says the phase's entries are met. The next route step sees the next
    phase as current."""
    observation = observe(config, project)
    now = phases.current(observation['brief'])
    if now is None:
        return None
    problems = phases.exit_problems(observation, now, uncovered_deliverable_terms(observation))
    if problems:
        return dict(phase=now['id'], closed=False, problems=problems[:6])
    reason = 'every need and deliverable it owns has a resolved unknown and no task of it is open'
    terra(config, project, 'brief', 'phase-close', now['id'], '--reason', reason)
    following = phases.current(terra(config, project, 'brief', 'show'))
    return dict(phase=now['id'], closed=True, next=following['id'] if following else None)


def decide_through_outages(client: Any, config: dict[str, Any], system: str, user: str, wait_seconds: int = 300, project: Path | None = None, root: Path | None = None,
                           health: Any = None):
    """A model server that is restarting is waited for (its supervisor brings it back in seconds), not a failed step."""
    from cg.backend_persistent_model_session_python.src.persistent_model_session import ModelTransportError, RejectedGeneration
    from . import ops
    outages = 0
    while True:
        try:
            return decide(client, config, system, user, project=project, root=root)
        except RejectedGeneration:
            raise
        except ModelTransportError as error:
            outages += 1
            run_root = Path(config['mizpah'].get('run_root') or '.')
            delay = ops.backoff_seconds(outages, cap=wait_seconds)
            ops.record_outage(run_root, 'controller', config['controller'], error, outages, task='briefing', waited_seconds=delay,
                              action='the briefing is asked for again after '+str(int(delay))+' s' if outages <= 5 else 'the sixth in a row: the step fails')
            if outages > 5:
                raise
            checker = health or ops.Health(config, run_root)
            if not checker.wait_for_model((config['controller'].get('endpoint') or {}).get('base_url'), wait_seconds=wait_seconds, attempt=outages):
                raise


def ready_order(project: Path, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The route's pickable tasks in the order the loop takes them: builders before readers, since a reading of
    a file whose builder is also ready would only block (logo_mark3 measured contrast of marks nine build tasks
    had not drawn yet)."""
    ready = [t for t in tasks if t.get('pickable') and t.get('map_id')]

    def builds(task: dict[str, Any]) -> bool:
        for uid in [task.get('map_id')]+[a.removeprefix('unknown:') for a in task.get('acceptance') or [] if str(a).startswith('unknown:')]:
            path = layout.map_root(project)/'unknowns'/(str(uid)+'.json')
            try:
                if 'creates ' in str(json.loads(path.read_text()).get('notes') or ''):
                    return True
            except (OSError, ValueError):
                continue
        return False
    return sorted(ready, key=lambda t: not builds(t))


def step(config: dict[str, Any], project: Path, journal: Path, mode: str) -> dict[str, Any]:
    """One controller step: observe, decide, guard (one resubmission), apply, journal."""
    project = project.resolve()
    system = config['mizpah']['controller_policy']
    client = model_client(config)
    # What each call cost: model, prompt/completion/cached tokens, so the run can say what its controller spent.
    usage: list[dict[str, Any]] = []

    def observe_usage(kind: str, payload: dict[str, Any]) -> None:
        if kind != 'model_response' or payload.get('status') != 200:
            return
        body = payload.get('body')
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                body = {}
        u = (body or {}).get('usage') or {}
        if u:
            usage.append(dict(model=(body or {}).get('model'), prompt=u.get('prompt_tokens'), completion=u.get('completion_tokens'),
                              cached=(u.get('prompt_tokens_details') or {}).get('cached_tokens'),
                              cost_ticks=u.get('cost_in_usd_ticks')))
    previous_observer = getattr(client, 'observer', None)
    client.observer = (lambda k, p: (observe_usage(k, p), previous_observer(k, p) if previous_observer else None)[0])
    observation = observe(config, project)
    notes = operator_notes(Path(journal).parent)
    if notes:
        observation['operator_notes'] = notes
    observation['cautions'] = last_cautions(journal)
    observation['reviewer_doubts'] = reviewer_doubts(journal)
    observation['workspaces'] = task_workspaces(Path(journal).parent)
    memory_file = Path(journal).parent/'memory.md'
    observation['memory'] = memory_file.read_text().strip() if memory_file.exists() else ''
    refusals: list[str] = []
    accepted = dict(unknowns=[], tasks=[], proposals=[], rebucket=[], unblock=[], retype=[], done=None, why='')
    record: dict[str, Any] = dict(mode=mode, observation=observation, attempts=[], usage=usage)
    looks = 0
    attempt = 0
    while attempt < 2:
        user = render_observation(observation, mode, refusals)
        try:
            decision, raw = decide_through_outages(client, config, system, user, project=project, root=Path(journal).parent)
        except ValueError as error:
            record['attempts'].append(dict(user=user, error=str(error), tools=list(_last_tools[0])))
            refusals = ['reply was not one JSON object: '+str(error)[:200]]
            attempt += 1
            continue
        attempt += 1
        accepted, refusals = guard(decision, observation, project, require_deliverables=(mode == 'route'))
        # The step's reflection is required — a manager's end-of-day line: what landed, what I did, what I am
        # watching for. A decision without one is refused like an unknown that cites nothing.
        memory = str(decision.get('memory') or '').strip() if isinstance(decision, dict) else ''
        if not memory:
            refusals = refusals+['no "memory": write your notes for the next step — what landed, what you did about it, what you are watching for']
        elif len(memory) > MEMORY_CHARS:
            refusals = refusals+['"memory" is '+str(len(memory))+' characters; keep it under '+str(MEMORY_CHARS)+' — notes, not a transcript']
        else:
            accepted['memory'] = memory
        # The message as sent, so a person can read the exchange the way the model saw it.
        record['attempts'].append(dict(user=user, raw=raw, reasoning=_last_reasoning[0], accepted=accepted, refusals=refusals,
                                       noted=accepted.get('noted') or [], observation_chars=len(user), tools=list(_last_tools[0])))
        record['noted'] = (record.get('noted') or [])+(accepted.get('noted') or [])
        minted_nothing = not any(accepted[k] for k in ('unknowns', 'tasks', 'proposals', 'rebucket', 'unblock', 'retype'))
        work_routed = any(t['status'] in ('ready', 'in_progress') for t in observation['tasks'])
        if minted_nothing and accepted.get('done') is not True and attempt == 1 and not work_routed:
            # It described what is owed but routed nothing, and nothing is waiting to run: ask once for the
            # unknowns or an explicit done. With work already routed, an empty reply is the right one — asking
            # again only made the controller list the waiting tasks a second time (a third of all refusals).
            refusals = refusals + ['you minted nothing and did not say "done": true — if the map still owes the '
                                   'brief something, mint the unknowns and tasks for it now; if nothing is owed, '
                                   'reply with "done": true']
            continue
        if not refusals or attempt == 2:
            break
        if any(accepted[k] for k in ('unknowns', 'tasks', 'proposals', 'rebucket', 'unblock', 'retype')):
            # Keep what passed; ask only about what did not.
            applied = apply(config, project, accepted, root=journal.parent)
            if accepted.get('memory'):
                memory_file.write_text(accepted['memory']+'\n')
            record.setdefault('applied', dict(unknowns=[], tasks=[], proposals=[], rebucket=[], unblock=[], retype=[]))
            for key in applied:   # `refused` appears only when Terra refused a create; it is not in the template
                record['applied'].setdefault(key, [])
                record['applied'][key] += applied[key]
            looked = observation.get('looked')
            observation = observe(config, project)
            if looked:
                observation['looked'] = looked
            observation['applied_so_far'] = {k: [str(x) for x in v] for k, v in record['applied'].items() if k in ('unknowns', 'tasks')}
            accepted = dict(unknowns=[], tasks=[], proposals=[], rebucket=[], unblock=[], retype=[], done=accepted.get('done'), why=accepted['why'])
    applied = apply(config, project, accepted, root=journal.parent)
    if accepted.get('memory'):
        memory_file.write_text(accepted['memory']+'\n')
    record.setdefault('applied', dict(unknowns=[], tasks=[], proposals=[], rebucket=[], unblock=[], retype=[]))
    for key in applied:
        record['applied'].setdefault(key, [])
        record['applied'][key] += applied[key]
    record['refused'] = refusals
    record['cautions'] = list(accepted.get('cautions') or [])
    record['why'] = accepted.get('why', '')
    record['done'] = accepted.get('done')
    record['answered_notes'] = [str(n.get('text') or '')[:200] for n in notes]
    mark_notes_read(Path(journal).parent, notes)
    # What the loop takes next, in its order: the briefing's handoff line.
    try:
        record['up_next'] = [t['id'] for t in ready_order(project, terra(config, project, 'route', 'next')['tasks'])]
    except Exception:  # noqa: BLE001 — a briefing detail never fails the step
        record['up_next'] = []
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open('a') as handle:
        handle.write(json.dumps(record)+'\n')
    (journal.parent/'controller.live.json').unlink(missing_ok=True)   # the record is the step now
    return dict(mode=mode, applied=record['applied'], refused=refusals, why=record['why'], done=accepted.get('done'))
