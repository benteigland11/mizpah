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

from . import briefs, capabilities, enablers, phases
from .worker import terra

ID_PATTERN = re.compile(r'^[a-z][a-z0-9_]*$')
TYPES = ('number', 'boolean', 'label')
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
    related_briefs = briefs.related(config, brief) if config['mizpah'].get('brief_library', True) else []
    registry = capabilities.render(config, brief)
    return dict(
        repo=repo_digest(project),
        brief={key: brief.get(key) for key in ('title', 'version', 'status', 'mission', 'needs', 'deliverables',
                                                'non_goals', 'enablers', 'budget_points', 'phases', 'open_proposals')}
              | dict(proposals=[p for p in json.loads((project/'.terra'/'brief.json').read_text()).get('proposals') or []
                                if p.get('status') in (None, 'open', 'pending')]),
        gate=sitrep.get('gate'), related_briefs=related_briefs, registry=registry,
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


def render_observation(observation: dict[str, Any], mode: str, refusals: list[str] = ()) -> str:
    brief = observation['brief']
    lines = ['# Brief (reference, v'+str(brief.get('version'))+', '+str(brief.get('status'))+')',
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
    for key in ('needs', 'deliverables', 'non_goals'):
        entries = brief.get(key) or []
        lines.append(key.capitalize()+':'+('' if entries else ' (none)'))
        for i, entry in enumerate(entries):
            ref = key[:-1].replace('non_goal', 'non-goal')+':'+str(i+1)
            lines.append('  '+ref+' '+str(entry)+((phases.tag(brief, ref)+enablers.tag(brief, ref)) if key != 'non_goals' else ''))
            if key == 'deliverables':
                for line in cited.get(ref, []):
                    lines.append('      ↳ '+line[:160])
                if not cited.get(ref):
                    lines.append('      ↳ (no unknown cites this deliverable)')
    if brief.get('budget_points') is not None:
        lines.append('Budget points: '+str(brief['budget_points']))
    proposals = brief.get('proposals') or []
    if proposals:
        lines.append('Open proposals (queued for the person; the map\'s record that a need cannot be met as written; '
                     'the project cannot be judged met while one is open):')
        for p in proposals:
            lines.append('  '+str(p.get('id'))+' '+str(p.get('summary') or '').split(' \u2014 evidence:')[0][:200])
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
    for k in observation['knowns']:
        value = k['mean'] if k['mean'] is not None else k['rate']
        if k['type'] == 'label':
            value = repr(k.get('mode')) if k.get('mode') is not None else None
        elif k['type'] == 'boolean' and value is not None:
            value = 'true' if float(value) >= 0.5 else 'false'
        elif isinstance(value, float):
            value = round(value, 4)
        stale = ' STALE: '+'; '.join(str(r)[:80] for r in k['stale_reasons'][:2]) if k.get('stale') else ''
        lines.append('  '+str(k['id'])+' = '+str(value)+' ('+str(k['confidence'])+', n='+str(k['n'])+')'+stale)
    if any(k.get('stale') for k in observation['knowns']):
        lines.append('A STALE known is no longer believed: a file it depends on changed after its readings. It is owed '
                     'again under the SAME id — route a task that lists the stale known\'s id among its unknowns; the '
                     'worker re-takes that reading with its probe. Never mint a new unknown for a claim the map already '
                     'holds: a second probe for the same question is shopping for an answer.')
    false_artifacts = [k for k in observation['knowns'] if k['type'] == 'boolean' and k.get('rate') is not None
                       and float(k['rate']) < 0.5 and not k.get('stale')]
    if false_artifacts:
        lines.append('A boolean that reads false about an artifact ('+', '.join(k['id'] for k in false_artifacts[:4])+') is '
                     'answered by changing the artifact, after which the known goes STALE and its id is routed again — not '
                     'by a new unknown with a new probe.')
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
                     'or asked. Route it again only with a different question or type; otherwise propose the change '
                     'and leave it — the artifacts that depend on the map may still be built.')
    lines.append('Route tasks:'+('' if observation['tasks'] else ' (none)'))
    finished = [t for t in observation['tasks'] if t['status'] in ('done', 'cancelled')]
    if finished:
        # Closed tasks are history the map already shows as knowns: ids only.
        lines.append('  done: '+', '.join(t['id'] for t in finished))
    for t in observation['tasks']:
        if t in finished:
            continue
        lines.append('  '+t['id']+' ['+t['status']+', '+str(t['bucket'])+'] → '+', '.join(t.get('unknowns') or [str(t['unknown'])])+': '+t['title']+
                     (' (blocked: '+t['blocked_reason']+')' if t.get('blocked_reason') else ''))
    if any(str(t.get('blocked_reason') or '').startswith(BUDGET_BLOCK) for t in observation['tasks']):
        lines.append('A task blocked on budget resumes from where it stopped if you re-bucket it.')
    if any(t['status'] == 'blocked' and not str(t.get('blocked_reason') or '').startswith(BUDGET_BLOCK)
           for t in observation['tasks']):
        lines.append('A task blocked by its worker with a reason means the source could not be read as the unknown '
                     'asks: the question needs a different source, or the brief needs to change. That is what '
                     'proposals are for; do not re-mint the same question. One exception: a source that did not exist '
                     'yet because another task builds it — once that task is done, release the blocked one with '
                     '"unblock": [{"task": "<id>", "after": "<the task that built it>"}].')
    budget = observation.get('budget') or {}
    if budget:
        lines.append('Points: budget '+str(budget.get('budget_points'))+', planned '+str(budget.get('points_plan'))+
                     ', done '+str(budget.get('points_done'))+', unallocated '+str(budget.get('points_remaining_budget'))+
                     ' — tasks draw on the budget (low 3, medium 8, high 21); Terra refuses a task the budget cannot cover.')
    lines.append('')
    now = phases.current(brief)
    scope = ' (phase '+now['id']+': its needs and deliverables are what is owed now; "done" means this phase is met)' if now else ''
    if mode == 'route':
        lines.append('Step: route. What does the map still owe the brief'+scope+'? Mint the unknowns and one task each.')
    else:
        lines.append('Step: project eval. A task just closed or the route is empty. Judge the map against the brief'+scope+': '
                     'new unknowns if something is still owed, proposals if the evidence shows the brief itself '
                     'should change, or nothing.')
    if refusals:
        lines.append('')
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


def _same_reading(a: str, b: str) -> bool:
    """Two claims are one reading when their content words (stemmed) overlap almost entirely."""
    wa, wb = briefs._stems(briefs._words(a)), briefs._stems(briefs._words(b))
    if len(wa) < 3 or len(wb) < 3:
        return False
    return len(wa & wb)/min(len(wa), len(wb)) >= 0.85


def stem(unknown_id: str) -> str:
    """`repair_docs_handwritten_v2`, `..._current` and `..._again` are one reading asked three times."""
    return RETRY_SUFFIX.sub('', unknown_id)


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
    unknowns, tasks, proposals, rebucket, unblock = [], [], [], [], []
    for item in decision.get('unknowns') or []:
        if not isinstance(item, dict):
            refusals.append('unknown entry is not an object'); continue
        uid = str(item.get('id') or '')
        # One unknown may serve several brief entries ("the report states the questions the data could not
        # answer" cites the deliverable and the needs it names); the first reference is the primary cite.
        refs = [r.strip() for r in re.split(r'[|,;]| and ', str(item.get('cites') or '')) if r.strip()]
        cites = refs[0] if refs else ''
        kind, _, index = cites.partition(':')
        if not ID_PATTERN.match(uid):
            refusals.append('unknown '+repr(uid)+': id must match ^[a-z][a-z0-9_]*$'); continue
        if uid in existing_unknowns:
            refusals.append('unknown '+uid+': already exists ('+str(existing_unknowns[uid]['status'])+')'); continue
        # The same reading minted a third time under a new suffix is a loop, not a plan: two attempts that came
        # back false or blocked mean the source or the brief is wrong, and that is a proposal (docs_page minted
        # repair_docs_handwritten_compliance, _v2 and _current in a row, 2026-09-19).
        same_claim = [u for u in existing_unknowns.values() if u['id'] != uid and u.get('type') == item.get('type')
                      and _same_reading(str(u.get('claim') or ''), str(item.get('claim') or ''))]
        if same_claim:
            refusals.append('unknown '+uid+': the map already holds this reading as '+same_claim[0]['id']+' ['+str(same_claim[0]['status'])
                            +']; if its artifact changed it goes stale and its own id is routed again, if it read false the '
                            'artifact is what changes — a second unknown for one claim is refused'); continue
        twins = [u for u in existing_unknowns.values() if stem(u['id']) == stem(uid) and u['id'] != uid]
        if len(twins) >= 2:
            refusals.append('unknown '+uid+': the third attempt at '+stem(uid)+' ('+', '.join(t['id'] for t in twins)+' already exist); '
                            'a reading that failed twice is not re-minted — propose the change to the need or deliverable '
                            'it cites, with the two readings as evidence, or leave it'); continue
        bad_refs = [r for r in refs if r.partition(':')[0] not in counts or not r.partition(':')[2].isdigit()
                    or not 1 <= int(r.partition(':')[2]) <= counts[r.partition(':')[0]]]
        if not refs or bad_refs:
            refusals.append('unknown '+uid+': cites '+repr(item.get('cites'))+' but the brief has '+str(counts['need'])+
                            ' needs and '+str(counts['deliverable'])+' deliverables; cite need:N or deliverable:N'
                            ' (several allowed, separated by |)'); continue
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
        # A non-goal names things not to be made (`a framework`, `bundler`, external fonts): an artifact unknown
        # that creates or claims one is refused. Readings are left alone — measuring that a non-goal is
        # respected (external_request_count = 0) is how the map proves it.
        offending = [term for non_goal in brief.get('non_goals') or [] for term in re.findall(r'`([^`]+)`', str(non_goal))
                     if term and (creates or cites.startswith('deliverable:')) and term.lower() in (creates+' '+claim).lower()]
        if offending:
            refusals.append('unknown '+uid+': it builds '+', '.join('`'+t+'`' for t in dict.fromkeys(offending))+', which the brief '
                            'names as a non-goal; a non-goal is respected, not delivered'); continue
        if creates and not source:
            source = creates
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
                             creates=creates, also=item.get('also') or [], enabler=item.get('enabler') or ''))
    # An artifact is verified by agreement with the map, so its unknown must say which knowns (or
    # unknowns minted alongside) its content agrees with. Without that anchor the probe can only check
    # that the file exists: a report with a table of invented stations passed on 2026-09-18.
    anchors = {k['id'] for k in observation['knowns']} | {u['id'] for u in observation['unknowns']} | {u['id'] for u in unknowns}
    for item in list(unknowns):
        # An artifact unknown is one that creates something, or a boolean citing a deliverable ("exits 0" against
        # `source: environment` is the existence check by another door). A number or label citing a deliverable
        # is a reading OF the artifact — contrast, line length — and needs no anchor; refusing those threw away
        # the design unknowns the controller had pulled from the library (docs_page, 2026-09-19).
        artifact = (item['creates'] or (item['cites'].startswith('deliverable:') and item.get('type') == 'boolean')) \
            and not item.get('enabler')
        if artifact and item.get('type') == 'label':
            # An artifact is verified by agreement with the map, never by recording what it prints.
            refusals.append('unknown '+item['id']+': an artifact unknown is an agreement, not a label — make it boolean '
                            '(the output equals the known it names) or number (the value it prints); a label is for a '
                            'reading of the data (which store, which file)')
            unknowns.remove(item); continue
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
        if artifact and not names_proposal and not named_files \
                and not [w for w in re.findall(r'[a-z][a-z0-9_]*', text) if w in anchors and w != item['id']]:
            refusals.append('unknown '+item['id']+': it is about '+(item['creates'] or item['cites'])+' but names no known '
                            'or unknown its content must agree with; an artifact is verified against the map — name '
                            'them in the evidence ("the STN01 row matches stn01_mean_temp_c", "the tests assert '
                            'station_count and mean_temp_c"), minting number unknowns first when the map lacks them; '
                            'a statement that a need cannot be answered is anchored on the open proposal that records '
                            'it (name its id, e.g. CR-001); a thing that is built (a page, a script) is anchored on the '
                            'source files it is built from (name them: "sections follow content/pitch.md"), and its '
                            'measured properties are separate unknowns that depend on the build')
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
        stale_ids = {k['id'] for k in observation['knowns'] if k.get('stale')}
        bad = [u for u in ids if u not in minted and u not in open_unknowns and u not in stale_ids]
        if bad:
            kept = [u for u in ids if u not in bad]
            if not kept:
                refusals.append('task '+tid+': unknown '+', '.join(repr(u) for u in bad)+' is neither minted here nor open'); continue
            # A task carrying several unknowns keeps the ones that passed; the refused ones were reported above.
            refusals.append('task '+tid+': dropped unknown '+', '.join(repr(u) for u in bad)+' (refused or absent); kept '+', '.join(kept))
            ids = kept
        taken = [u for u in ids if u in routed or any(u in t['unknowns'] for t in tasks)]
        if taken:
            kept = [u for u in ids if u not in taken]
            if not kept:
                refusals.append('task '+tid+': unknown '+', '.join(taken)+' already has an open task'); continue
            # One unknown already routed does not sink the others the task carries.
            refusals.append('task '+tid+': dropped unknown '+', '.join(taken)+' (already has an open task); kept '+', '.join(kept))
            ids = kept
        if item.get('bucket') not in BUCKETS:
            refusals.append('task '+tid+': bucket must be one of '+', '.join(BUCKETS)); continue
        deps = [str(d) for d in item.get('deps') or []]
        bad = [d for d in deps if d not in existing_tasks and not any(t['id'] == d for t in tasks)]
        if bad:
            # A dependency on a task refused above (or never named) is dropped, not fatal: one bad task
            # otherwise sinks every task behind it and every unknown they carried (components, 2026-09-19).
            refusals.append('task '+tid+': dropped dependency '+', '.join(bad)+' (refused or absent); kept the task')
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
        if not builds:
            mine = ' '.join(str(u.get('source') or '')+' '+str(u.get('claim') or '') for u in unknowns if u['id'] in ids).lower()
            for made, owner_task in creators.items():
                if owner_task and owner_task != tid and made and made in mine and owner_task not in deps:
                    deps.append(owner_task)
                    refusals.append('task '+tid+': reads '+made+', which '+owner_task+' builds — added that dependency')
            # A reading of a path a deliverable names that does not exist yet, with nothing routed to build it, is
            # a task that can only block ("count the candidates under brand/marks/" before any were drawn).
            if project is not None and not deps:
                named = {p.rstrip('/').lower() for text in (observation['brief'].get('deliverables') or [])
                         for p in re.findall(r'[\w./-]+/[\w./-]*|[\w./-]+\.[A-Za-z0-9]+', str(text))}
                missing = sorted({p for p in named if p and p in mine and not (project/p).exists()
                                  and not any(p in c for c in creators)})
                if missing:
                    refusals.append('task '+tid+': reads '+', '.join(missing[:3])+', which does not exist and no task builds — '
                                    'mint the artifact unknown that creates it (with `creates`) and its task first, and make '
                                    'this task depend on it'); continue
        made_here = {u['creates'].lower() for u in unknowns if u['id'] in ids and u.get('creates')}
        def reads_own(task_unknowns: list[str]) -> bool:
            texts = ' '.join(str(u.get('source') or '')+' '+str(u.get('claim') or '') for u in unknowns if u['id'] in task_unknowns)
            texts += ' '.join(str(u.get('claim') or '')+' '+str(u.get('notes') or '') for u in observation['unknowns'] if u['id'] in task_unknowns)
            return any(m and m in texts.lower() for m in made_here)
        reading_tasks = [t['id'] for t in tasks if not any(u in artifact_ids for u in t['unknowns']) and not reads_own(t['unknowns'])]
        reading_tasks += [t['id'] for t in observation['tasks'] if t['status'] in OPEN_TASK
                          and not any(u in artifact_ids for u in (t.get('unknowns') or [])) and not reads_own(t.get('unknowns') or [])]
        if builds and not deps and reading_tasks:
            # An artifact that must agree with the map cannot be built before the readings exist.
            refusals.append('task '+tid+': it builds an artifact that must agree with the map, so it depends on the '
                            'tasks that produce those knowns; add deps from: '+', '.join(dict.fromkeys(reading_tasks))); continue
        carried = {u['enabler'] for u in unknowns if u['id'] in ids and u.get('enabler')}
        if len(carried) > 1:
            refusals.append('task '+tid+': one enabler per task ('+', '.join(sorted(carried))+')'); continue
        tasks.append(dict(id=tid, title=title, unknowns=ids, unknown=ids[0], bucket=item['bucket'], deps=deps,
                          enabler=next(iter(carried), '')))
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
    done = decision.get('done')
    done = bool(done) if isinstance(done, bool) else None
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
    return dict(unknowns=unknowns, tasks=tasks, proposals=proposals, rebucket=rebucket, unblock=unblock,
                done=done, why=str(decision.get('why') or '')), refusals


def apply(config: dict[str, Any], project: Path, accepted: dict[str, Any]) -> dict[str, list[str]]:
    """Write the accepted decision through Terra; proposals are queued, never accepted here."""
    done = dict(unknowns=[], tasks=[], proposals=[], rebucket=[], unblock=[])
    for u in accepted['unknowns']:
        args = ['unknown', 'create', u['id'], '--claim', u['claim'], '--evidence', u['evidence_needed'],
                '--type', u['type'], '--quantity', u['quantity'],
                '--notes', 'cites '+u['cites']+('; also '+', '.join(u['also']) if u.get('also') else '')
                +('; source '+u['source'] if u.get('source') else '')
                +('; creates '+u['creates'] if u.get('creates') else '')
                +('; enabler '+u['enabler'] if u.get('enabler') else '')]
        if u['unit']:
            args += ['--unit', u['unit']]
        terra(config, project, *args)
        done['unknowns'].append(u['id'])
    now = phases.current(terra(config, project, 'brief', 'show')) if accepted['tasks'] else None
    sectors = set()
    if now and (project/'.terra'/'route.json').exists():
        sectors = {s.get('id') for s in json.loads((project/'.terra'/'route.json').read_text()).get('sectors') or []}
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
        for key in ('need', 'deliverable', 'non_goal', 'mission'):
            if p.get(key):
                args += ['--'+key.replace('_', '-'), p[key]]
        terra(config, project, *args)
        done['proposals'].append(p['summary'])
    for r in accepted.get('rebucket') or []:
        terra(config, project, 'route', 'set-effort', r['task'], '--bucket', r['bucket'])
        terra(config, project, 'route', 'unblock', r['task'])
        done['rebucket'].append(r['task']+'→'+r['bucket'])
    for r in accepted.get('unblock') or []:
        terra(config, project, 'route', 'unblock', r['task'])
        record_release(project, r['task'], r['after'])
        done.setdefault('unblock', []).append(r['task']+' after '+r['after'])
    return done


def released_before(project: Path | None) -> set[tuple[str, str]]:
    """(task, after) pairs the loop has already released, from the project's own record."""
    if project is None:
        return set()
    path = project/'.terra'/'.mizpah-unblocks.json'
    try:
        return {tuple(p) for p in json.loads(path.read_text())}
    except (OSError, ValueError):
        return set()


def record_release(project: Path, task: str, after: str) -> None:
    path = project/'.terra'/'.mizpah-unblocks.json'
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


def decide_through_outages(client: Any, config: dict[str, Any], system: str, user: str, wait_seconds: int = 300,
                           health: Any = None):
    """A model server that is restarting is waited for (its supervisor brings it back in seconds), not a failed step."""
    from cg.backend_persistent_model_session_python.src.persistent_model_session import ModelTransportError, RejectedGeneration
    from . import ops
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
            checker = health or ops.Health(config, Path(config['mizpah'].get('run_root') or '.'))
            if not checker.wait_for_model((config['controller'].get('endpoint') or {}).get('base_url'), wait_seconds=wait_seconds):
                raise


def step(config: dict[str, Any], project: Path, journal: Path, mode: str) -> dict[str, Any]:
    """One controller step: observe, decide, guard (one resubmission), apply, journal."""
    project = project.resolve()
    system = config['mizpah']['route_policy'] if mode == 'route' else config['mizpah']['eval_policy']
    client = model_client(config)
    observation = observe(config, project)
    refusals: list[str] = []
    accepted = dict(unknowns=[], tasks=[], proposals=[], rebucket=[], unblock=[], done=None, why='')
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
                and not any(decision.get(k) for k in ('unknowns', 'tasks', 'proposals', 'rebucket', 'unblock')):
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
        record['attempts'].append(dict(raw=raw, accepted=accepted, refusals=refusals, observation_chars=len(user)))
        minted_nothing = not any(accepted[k] for k in ('unknowns', 'tasks', 'proposals', 'rebucket', 'unblock'))
        if minted_nothing and accepted.get('done') is not True and attempt == 1:
            # It described what is owed but routed nothing: ask once for the unknowns or an explicit done.
            refusals = refusals + ['you minted nothing and did not say "done": true — if the map still owes the '
                                   'brief something, mint the unknowns and tasks for it now; if nothing is owed, '
                                   'reply with "done": true']
            continue
        if not refusals or attempt == 2:
            break
        if any(accepted[k] for k in ('unknowns', 'tasks', 'proposals', 'rebucket', 'unblock')):
            # Keep what passed; ask only about what did not.
            applied = apply(config, project, accepted)
            record.setdefault('applied', dict(unknowns=[], tasks=[], proposals=[], rebucket=[], unblock=[]))
            for key in applied:
                record['applied'][key] += applied[key]
            looked = observation.get('looked')
            observation = observe(config, project)
            if looked:
                observation['looked'] = looked
            accepted = dict(unknowns=[], tasks=[], proposals=[], rebucket=[], unblock=[], done=accepted.get('done'), why=accepted['why'])
    applied = apply(config, project, accepted)
    record.setdefault('applied', dict(unknowns=[], tasks=[], proposals=[], rebucket=[], unblock=[]))
    for key in applied:
        record['applied'][key] += applied[key]
    record['refused'] = refusals
    record['why'] = accepted.get('why', '')
    record['done'] = accepted.get('done')
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open('a') as handle:
        handle.write(json.dumps(record)+'\n')
    return dict(mode=mode, applied=record['applied'], refused=refusals, why=record['why'], done=accepted.get('done'))
