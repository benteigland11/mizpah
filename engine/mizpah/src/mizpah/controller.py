"""The controller: brief and observation in, unknowns, tasks and proposals out.

One bare model call per step and no tools. The controller never sees a worker transcript
and never writes to the map; it mints the error signal (unknowns, each citing the brief
entry it serves) and routes it (one task per unknown), and it may propose brief changes
with evidence. Everything it emits passes a structural guard before Terra sees it, and
proposals are only ever queued — the person accepts them.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Any

from cg.bp_focused_agent_session_python.src import EndpointConfig, ModelClient, llama_model_client
from cg.backend_persistent_model_session_python.src.persistent_model_session import parse_turn

from .worker import terra

ID_PATTERN = re.compile(r'^[a-z][a-z0-9_]*$')
TYPES = ('number', 'boolean')
BUCKETS = ('low', 'medium', 'high')
OPEN_UNKNOWN = ('open', 'probing', 'blocked')
OPEN_TASK = ('ready', 'in_progress', 'blocked', 'pending')
BUDGET_BLOCK = ('worker budget exhausted', 'gate rounds exhausted')


DIGEST_SKIP = {'.terra', '.git', '.venv', '__pycache__', 'node_modules', '.playbook', '.tool-output', '.session-history', 'cg'}
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


def observe(config: dict[str, Any], project: Path) -> dict[str, Any]:
    """Everything the controller reads, bounded: the brief and a digest of the map and route."""
    brief = terra(config, project, 'brief', 'show')
    sitrep = terra(config, project, 'sitrep')
    # `known list --json` nests each known's fields under `record`.
    knowns = []
    for row in json.loads(subprocess.run([config['mizpah']['terra'], 'known', 'list', '--json'], cwd=project,
                                         capture_output=True, text=True).stdout or '[]'):
        record = dict(row.get('record') or row)
        record['stale'], record['stale_reasons'] = bool(row.get('stale')), list(row.get('stale_reasons') or [])
        knowns.append(record)
    unknowns = [json.loads(path.read_text()) for path in sorted((project/'.terra'/'map'/'unknowns').glob('*.json'))]
    route = terra(config, project, 'route', 'status')
    return dict(
        repo=repo_digest(project),
        brief={key: brief.get(key) for key in ('title', 'version', 'status', 'mission', 'needs', 'deliverables',
                                                'non_goals', 'enablers', 'budget_points', 'phases', 'open_proposals')}
              | dict(proposals=[p for p in json.loads((project/'.terra'/'brief.json').read_text()).get('proposals') or []
                                if p.get('status') in (None, 'open', 'pending')]),
        gate=sitrep.get('gate'),
        budget=(sitrep.get('route') or {}).get('budget'),
        knowns=[dict(id=k.get('id'), type=k.get('type'), status=k.get('status'), confidence=k.get('confidence'),
                     n=(k.get('stats') or {}).get('n'), mean=(k.get('stats') or {}).get('mean'),
                     rate=(k.get('stats') or {}).get('rate'), claim=k.get('claim'),
                     stale=k.get('stale', False), stale_reasons=k.get('stale_reasons') or []) for k in knowns],
        unknowns=[dict(id=u['id'], status=u.get('status'), type=u.get('type'), quantity=u.get('quantity'),
                       claim=u.get('claim'), resolved_by=u.get('resolved_by'), notes=u.get('notes')) for u in unknowns],
        tasks=[dict(id=t['id'], status=t['status'], unknown=t.get('map_id'),
                    unknowns=[t.get('map_id')]+[a.removeprefix('unknown:') for a in t.get('acceptance') or []
                                                if a.startswith('unknown:')],
                    bucket=t.get('bucket'), title=t['title'], blocked_reason=t.get('blocked_reason'))
               for t in route.get('tasks') or []],
    )


def render_observation(observation: dict[str, Any], mode: str, refusals: list[str] = ()) -> str:
    brief = observation['brief']
    lines = ['# Brief (reference, v'+str(brief.get('version'))+', '+str(brief.get('status'))+')',
             'Mission: '+str(brief.get('mission'))]
    cited: dict[str, list[str]] = {}
    for u in observation['unknowns']:
        notes = str(u.get('notes') or '')
        ref = notes.split('cites ', 1)[1].split(';')[0].strip() if 'cites ' in notes else ''
        if ref:
            cited.setdefault(ref, []).append(u['id']+' ['+str(u.get('status'))+']: '+str(u.get('claim')))
    for key in ('needs', 'deliverables', 'non_goals'):
        entries = brief.get(key) or []
        lines.append(key.capitalize()+':'+('' if entries else ' (none)'))
        for i, entry in enumerate(entries):
            ref = key[:-1].replace('non_goal', 'non-goal')+':'+str(i+1)
            lines.append('  '+ref+' '+str(entry))
            if key == 'deliverables':
                for line in cited.get(ref, []):
                    lines.append('      ↳ '+line[:160])
                if not cited.get(ref):
                    lines.append('      ↳ (no unknown cites this deliverable)')
    if brief.get('budget_points') is not None:
        lines.append('Budget points: '+str(brief['budget_points']))
    lines.append('')
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
    for k in observation['knowns']:
        value = k['mean'] if k['mean'] is not None else k['rate']
        if k['type'] == 'boolean' and value is not None:
            value = 'true' if float(value) >= 0.5 else 'false'
        elif isinstance(value, float):
            value = round(value, 4)
        stale = ' STALE: '+'; '.join(str(r)[:80] for r in k['stale_reasons'][:2]) if k.get('stale') else ''
        lines.append('  '+str(k['id'])+' = '+str(value)+' ('+str(k['confidence'])+', n='+str(k['n'])+')'+stale)
    if any(k.get('stale') for k in observation['knowns']):
        lines.append('A STALE known is no longer believed: a file it depends on changed after its readings. It is owed '
                     'again — mint an unknown that re-takes the reading (its artifact may have regressed) and a task for it.')
    open_unknowns = [u for u in observation['unknowns'] if u['status'] in OPEN_UNKNOWN]
    resolved = len(observation['unknowns'])-len(open_unknowns)
    lines.append('Open unknowns:'+('' if open_unknowns else ' (none)')+(' — '+str(resolved)+' resolved' if resolved else ''))
    for u in open_unknowns:
        lines.append('  '+u['id']+' ['+str(u['status'])+'] '+str(u['claim']))
    lines.append('Route tasks:'+('' if observation['tasks'] else ' (none)'))
    for t in observation['tasks']:
        lines.append('  '+t['id']+' ['+t['status']+', '+str(t['bucket'])+'] → '+', '.join(t.get('unknowns') or [str(t['unknown'])])+': '+t['title']+
                     (' (blocked: '+t['blocked_reason']+')' if t.get('blocked_reason') else ''))
    if any(str(t.get('blocked_reason') or '').startswith(BUDGET_BLOCK) for t in observation['tasks']):
        lines.append('A task blocked on budget resumes from where it stopped if you re-bucket it.')
    if any(t['status'] == 'blocked' and not str(t.get('blocked_reason') or '').startswith(BUDGET_BLOCK)
           for t in observation['tasks']):
        lines.append('A task blocked by its worker with a reason means the source could not be read as the unknown '
                     'asks: the question needs a different source, or the brief needs to change. That is what '
                     'proposals are for; do not re-mint the same question.')
    budget = observation.get('budget') or {}
    if budget:
        lines.append('Points: budget '+str(budget.get('budget_points'))+', planned '+str(budget.get('points_plan'))+
                     ', done '+str(budget.get('points_done'))+', unallocated '+str(budget.get('points_remaining_budget'))+
                     ' — tasks draw on the budget (low 3, medium 8, high 21); Terra refuses a task the budget cannot cover.')
    lines.append('')
    if mode == 'route':
        lines.append('Step: route. What does the map still owe the brief? Mint the unknowns and one task each.')
    else:
        lines.append('Step: project eval. A task just closed or the route is empty. Judge the map against the brief: '
                     'new unknowns if something is still owed, proposals if the evidence shows the brief itself '
                     'should change, or nothing.')
    if refusals:
        lines.append('')
        lines.append('Your previous reply had items refused by the guard; resubmit only corrected items, or fewer:')
        lines += ['  - '+r for r in refusals]
    return '\n'.join(lines)+'\n'


def model_client(config: dict[str, Any]) -> ModelClient:
    spec = config['controller']
    endpoint = EndpointConfig(**spec['endpoint'])
    if spec.get('provider', 'direct_json') == 'llama_client':
        return llama_model_client(endpoint, known_issues=spec.get('known_issues'))
    return ModelClient(endpoint)


def decide(client: ModelClient, config: dict[str, Any], system: str, user: str) -> tuple[dict[str, Any], str]:
    """One completion, parsed as the JSON object it was asked for; raw text kept for the journal."""
    payload = dict(config['controller']['generation'], messages=[dict(role='system', content=system),
                                                                   dict(role='user', content=user)],
                   max_tokens=config['mizpah'].get('controller_output_tokens', 8192))
    response = client.complete(payload, 'controller')
    content = (parse_turn(response).message.get('content') or '').strip()
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


def uncovered_deliverable_terms(observation: dict[str, Any], extra_unknowns: list[dict[str, Any]] = ()) -> list[str]:
    """Backticked names in each deliverable that no unknown citing it mentions.

    The brief's own backticks are its vocabulary of named things (commands, files, invocations);
    a deliverable is covered only when each of them appears in the id or claim of some unknown
    that cites that deliverable. Mechanical, so a broad claim cannot paper over a missing command.
    """
    problems: list[str] = []
    unknowns = list(observation['unknowns'])+[dict(id=u['id'], claim=u['claim'], notes='cites '+u['cites']) for u in extra_unknowns]
    for index, text in enumerate(observation['brief'].get('deliverables') or [], start=1):
        ref = 'deliverable:'+str(index)
        citing = ' '.join((u['id']+' '+str(u.get('claim') or '')).lower() for u in unknowns
                          if ('cites '+ref) in str(u.get('notes') or ''))
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


def guard(decision: dict[str, Any], observation: dict[str, Any], project: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    """Structural floor: every unknown cites a brief entry and names a source that exists; every task
    resolves an open unknown. The quantity is the unknown id — the controller does not pick a second name."""
    refusals: list[str] = []
    brief = observation['brief']
    counts = dict(need=len(brief.get('needs') or []), deliverable=len(brief.get('deliverables') or []))
    existing_unknowns = {u['id']: u for u in observation['unknowns']}
    existing_tasks = {t['id'] for t in observation['tasks']}
    open_unknowns = {u['id'] for u in observation['unknowns'] if u['status'] in OPEN_UNKNOWN}
    routed = {u for t in observation['tasks'] if t['status'] in OPEN_TASK for u in (t.get('unknowns') or [t['unknown']])}
    unknowns, tasks, proposals, rebucket = [], [], [], []
    for item in decision.get('unknowns') or []:
        if not isinstance(item, dict):
            refusals.append('unknown entry is not an object'); continue
        uid = str(item.get('id') or '')
        cites = str(item.get('cites') or '')
        kind, _, index = cites.partition(':')
        if not ID_PATTERN.match(uid):
            refusals.append('unknown '+repr(uid)+': id must match ^[a-z][a-z0-9_]*$'); continue
        if uid in existing_unknowns:
            refusals.append('unknown '+uid+': already exists ('+str(existing_unknowns[uid]['status'])+')'); continue
        if kind not in counts or not index.isdigit() or not 1 <= int(index) <= counts[kind]:
            refusals.append('unknown '+uid+': cites '+repr(cites)+' but the brief has '+str(counts['need'])+
                            ' needs and '+str(counts['deliverable'])+' deliverables; cite need:N or deliverable:N'); continue
        if item.get('type') not in TYPES:
            refusals.append('unknown '+uid+': type must be one of '+', '.join(TYPES)); continue
        claim, evidence_needed = str(item.get('claim') or '').strip(), str(item.get('evidence_needed') or '').strip()
        source = str(item.get('source') or '').strip()
        creates = str(item.get('creates') or '').strip()
        if not claim:
            refusals.append('unknown '+uid+': claim is required'); continue
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
        if creates and not source:
            source = creates
        if project is not None and source and not creates and not source_exists(project, source) and cites.startswith('deliverable:') \
                and relative_path(source):
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
                             creates=creates))
    # An artifact is verified by agreement with the map, so its unknown must say which knowns (or
    # unknowns minted alongside) its content agrees with. Without that anchor the probe can only check
    # that the file exists: a report with a table of invented stations passed on 2026-09-18.
    anchors = {k['id'] for k in observation['knowns']} | {u['id'] for u in observation['unknowns']} | {u['id'] for u in unknowns}
    for item in list(unknowns):
        # Citing a deliverable makes it an artifact unknown whether or not `creates` was set; "exits 0"
        # against `source: environment` is the same existence check by another door.
        artifact = item['creates'] or item['cites'].startswith('deliverable:')
        if artifact and not [w for w in re.findall(r'[a-z][a-z0-9_]*', item['claim']+' '+item['evidence_needed'])
                             if w in anchors and w != item['id']]:
            refusals.append('unknown '+item['id']+': it is about '+(item['creates'] or item['cites'])+' but names no known '
                            'or unknown its content must agree with; an artifact is verified against the map — name '
                            'them in the evidence ("the STN01 row matches stn01_mean_temp_c", "the tests assert '
                            'station_count and mean_temp_c"), minting number unknowns first when the map lacks them')
            unknowns.remove(item)
    minted = {u['id'] for u in unknowns}
    for item in decision.get('tasks') or []:
        if not isinstance(item, dict):
            refusals.append('task entry is not an object'); continue
        tid = str(item.get('id') or '')
        listed = item.get('unknowns')
        if not listed and item.get('unknown'):
            listed = [item.get('unknown')]
        ids = [str(u) for u in (listed or [])]
        if not ID_PATTERN.match(tid):
            refusals.append('task '+repr(tid)+': id must match ^[a-z][a-z0-9_]*$'); continue
        if tid in existing_tasks or any(t['id'] == tid for t in tasks):
            refusals.append('task '+tid+': already exists'); continue
        if not ids or len(set(ids)) != len(ids):
            refusals.append('task '+tid+': list the unknowns it resolves (one or more, no repeats)'); continue
        bad = [u for u in ids if u not in minted and u not in open_unknowns]
        if bad:
            kept = [u for u in ids if u not in bad]
            if not kept:
                refusals.append('task '+tid+': unknown '+', '.join(repr(u) for u in bad)+' is neither minted here nor open'); continue
            # A task carrying several unknowns keeps the ones that passed; the refused ones were reported above.
            refusals.append('task '+tid+': dropped unknown '+', '.join(repr(u) for u in bad)+' (refused or absent); kept '+', '.join(kept))
            ids = kept
        taken = [u for u in ids if u in routed or any(u in t['unknowns'] for t in tasks)]
        if taken:
            refusals.append('task '+tid+': unknown '+', '.join(taken)+' already has an open task'); continue
        if item.get('bucket') not in BUCKETS:
            refusals.append('task '+tid+': bucket must be one of '+', '.join(BUCKETS)); continue
        deps = [str(d) for d in item.get('deps') or []]
        bad = [d for d in deps if d not in existing_tasks and not any(t['id'] == d for t in tasks)]
        if bad:
            refusals.append('task '+tid+': unknown dependency '+', '.join(bad)); continue
        title = str(item.get('title') or '').strip()
        if not title:
            refusals.append('task '+tid+': title is required'); continue
        artifact_ids = {u['id'] for u in unknowns if u.get('creates')} | {
            u['id'] for u in observation['unknowns'] if 'creates ' in str(u.get('notes') or '')}
        builds = any(u in artifact_ids for u in ids)
        reading_tasks = [t['id'] for t in tasks if not any(u in artifact_ids for u in t['unknowns'])]
        reading_tasks += [t['id'] for t in observation['tasks'] if t['status'] in OPEN_TASK
                          and not any(u in artifact_ids for u in (t.get('unknowns') or []))]
        if builds and not deps and reading_tasks:
            # An artifact that must agree with the map cannot be built before the readings exist.
            refusals.append('task '+tid+': it builds an artifact that must agree with the map, so it depends on the '
                            'tasks that produce those knowns; add deps from: '+', '.join(dict.fromkeys(reading_tasks))); continue
        tasks.append(dict(id=tid, title=title, unknowns=ids, unknown=ids[0], bucket=item['bucket'], deps=deps))
    covered = {u for t in tasks for u in t['unknowns']}
    for uid in minted - covered:
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
        if not fields:
            refusals.append(label+': changes nothing (need, deliverable, non_goal or mission)'); continue
        if any(re.fullmatch(r'(need|deliverable):?\s*\d+', v) for v in fields.values()):
            refusals.append(label+': a proposal carries the new text of the need or deliverable, not its number'); continue
        if str(item['summary']).strip().lower() in open_summaries:
            refusals.append(label+': an open proposal already says this; wait for the person to decide'); continue
        proposals.append(dict(summary=str(item['summary']).strip(), evidence=str(item['evidence']).strip(), **fields))
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
    done = decision.get('done')
    done = bool(done) if isinstance(done, bool) else None
    if done is True:
        uncovered = uncovered_deliverable_terms(observation, unknowns)
        if uncovered:
            done = False
            refusals.append('done refused: '+'; '.join(uncovered)+' — mint one unknown per named thing (with `creates`), '
                            'each claim naming it and the known it must agree with')
    return dict(unknowns=unknowns, tasks=tasks, proposals=proposals, rebucket=rebucket,
                done=done, why=str(decision.get('why') or '')), refusals


def apply(config: dict[str, Any], project: Path, accepted: dict[str, Any]) -> dict[str, list[str]]:
    """Write the accepted decision through Terra; proposals are queued, never accepted here."""
    done = dict(unknowns=[], tasks=[], proposals=[], rebucket=[])
    for u in accepted['unknowns']:
        args = ['unknown', 'create', u['id'], '--claim', u['claim'], '--evidence', u['evidence_needed'],
                '--type', u['type'], '--quantity', u['quantity'],
                '--notes', 'cites '+u['cites']+('; source '+u['source'] if u.get('source') else '')
                +('; creates '+u['creates'] if u.get('creates') else '')]
        if u['unit']:
            args += ['--unit', u['unit']]
        terra(config, project, *args)
        done['unknowns'].append(u['id'])
    for t in accepted['tasks']:
        ids = t.get('unknowns') or [t['unknown']]
        args = ['route', 'add', t['id'], '--title', t['title'], '--map', ids[0], '--bucket', t['bucket'],
                '--skill', 'terra-probe']
        for extra in ids[1:]:
            args += ['--accept', 'unknown:'+extra]  # the task resolves these too; Terra's map_id holds only one
        for dep in t['deps']:
            args += ['--dep', dep]
        try:
            terra(config, project, *args)
        except RuntimeError as error:
            # Terra refused (usually the plan exceeding budget_points): the refusal is state, not a crash.
            done.setdefault('refused', []).append(t['id']+': '+str(error)[:300])
            continue
        done['tasks'].append(t['id'])
    for p in accepted['proposals']:
        args = ['brief', 'propose', '--summary', p['summary']+' — evidence: '+p['evidence']]
        for key in ('need', 'deliverable', 'non_goal', 'mission'):
            if p.get(key):
                args += ['--'+key.replace('_', '-'), p[key]]
        terra(config, project, *args)
        done['proposals'].append(p['summary'])
    for r in accepted.get('rebucket') or []:
        terra(config, project, 'route', 'set-effort', r['task'], '--bucket', r['bucket'])
        terra(config, project, 'route', 'unblock', r['task'])
        done['rebucket'].append(r['task']+'→'+r['bucket'])
    return done


def decide_through_outages(client: Any, config: dict[str, Any], system: str, user: str, wait_seconds: int = 300):
    """A model server that is restarting is waited for (its supervisor brings it back in seconds), not a failed step."""
    import time
    import urllib.request
    from cg.backend_persistent_model_session_python.src.persistent_model_session import ModelTransportError, RejectedGeneration
    outages = 0
    while True:
        try:
            return decide(client, config, system, user)
        except RejectedGeneration:
            raise
        except ModelTransportError:
            outages += 1
            if outages > 5:
                raise
            deadline = time.time()+wait_seconds
            while time.time() <= deadline:
                try:
                    with urllib.request.urlopen(config['controller']['endpoint']['base_url'].rstrip('/')+'/health', timeout=5) as r:
                        if r.status == 200:
                            break
                except OSError:
                    pass
                time.sleep(5)


def step(config: dict[str, Any], project: Path, journal: Path, mode: str) -> dict[str, Any]:
    """One controller step: observe, decide, guard (one resubmission), apply, journal."""
    project = project.resolve()
    system = config['mizpah']['route_policy'] if mode == 'route' else config['mizpah']['eval_policy']
    client = model_client(config)
    observation = observe(config, project)
    refusals: list[str] = []
    accepted = dict(unknowns=[], tasks=[], proposals=[], rebucket=[], done=None, why='')
    record: dict[str, Any] = dict(mode=mode, observation=observation, attempts=[])
    looks = 0
    attempt = 0
    while attempt < 2:
        user = render_observation(observation, mode, refusals)
        try:
            decision, raw = decide_through_outages(client, config, system, user)
        except ValueError as error:
            record['attempts'].append(dict(error=str(error)))
            refusals = ['reply was not one JSON object: '+str(error)[:200]]
            attempt += 1
            continue
        wants = decision.get('look') if isinstance(decision, dict) else None
        if isinstance(wants, list) and wants and looks < LOOK_ROUNDS \
                and not any(decision.get(k) for k in ('unknowns', 'tasks', 'proposals', 'rebucket')):
            # A look costs no attempt: the controller reads before it decides, up to LOOK_ROUNDS times.
            looks += 1
            looked = observation.setdefault('looked', {})
            refused = read_looks(project, wants, looked)
            record['attempts'].append(dict(raw=raw, look=[str(w) for w in wants][:LOOK_PATHS], why=str(decision.get('why') or '')[:300],
                                           refused=refused))
            refusals = refused
            continue
        attempt += 1
        accepted, refusals = guard(decision, observation, project)
        record['attempts'].append(dict(raw=raw, accepted=accepted, refusals=refusals))
        minted_nothing = not any(accepted[k] for k in ('unknowns', 'tasks', 'proposals', 'rebucket'))
        if minted_nothing and accepted.get('done') is not True and attempt == 1:
            # It described what is owed but routed nothing: ask once for the unknowns or an explicit done.
            refusals = refusals + ['you minted nothing and did not say "done": true — if the map still owes the '
                                   'brief something, mint the unknowns and tasks for it now; if nothing is owed, '
                                   'reply with "done": true']
            continue
        if not refusals or attempt == 2:
            break
        if any(accepted[k] for k in ('unknowns', 'tasks', 'proposals', 'rebucket')):
            # Keep what passed; ask only about what did not.
            applied = apply(config, project, accepted)
            record.setdefault('applied', dict(unknowns=[], tasks=[], proposals=[], rebucket=[]))
            for key in applied:
                record['applied'][key] += applied[key]
            looked = observation.get('looked')
            observation = observe(config, project)
            if looked:
                observation['looked'] = looked
            accepted = dict(unknowns=[], tasks=[], proposals=[], rebucket=[], done=accepted.get('done'), why=accepted['why'])
    applied = apply(config, project, accepted)
    record.setdefault('applied', dict(unknowns=[], tasks=[], proposals=[], rebucket=[]))
    for key in applied:
        record['applied'][key] += applied[key]
    record['refused'] = refusals
    record['why'] = accepted.get('why', '')
    record['done'] = accepted.get('done')
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open('a') as handle:
        handle.write(json.dumps(record)+'\n')
    return dict(mode=mode, applied=record['applied'], refused=refusals, why=record['why'], done=accepted.get('done'))
