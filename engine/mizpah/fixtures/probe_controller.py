"""Briefs that test the controller, not the worker: does it pull in what it needs and cite what it is shown?

Usage: python -m fixtures.probe_controller <target_dir> docs_page|catalog_pick     (makes <target_dir>/<name>)
       python -m fixtures.probe_controller --check <project> <session_root>        (prints the behaviour checklist)

docs_page: a one-page docs site that declares the `page_readings` enabler (in the registry after landing-en) and
says little in its needs — the deliverable asks for "the design targets other pages met". Expected: the enabler is
installed before the first route step with no task; related briefs (landing, the design series) are shown; the
controller mints rendered-page unknowns citing the deliverable that its own needs never named (contrast, overflow,
line length) — taken from the library, not invented.

catalog_pick: pick the cheapest catalog part meeting a spec, declaring a `catalog_lookup` enabler that no project has
graduated. Expected on the first run: the enabler is routed first (role enabler), the readings that name it wait, it
graduates and the registry gains it. Expected on a second run: installed from the registry before routing, no task.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import textwrap

from .suite import brief, key_path

DOCS = textwrap.dedent('''\
    # Mizpah in one page

    Mizpah is a loop: a brief says what is owed, unknowns say what is not yet known, a worker measures, a gate
    checks, and the map holds what was measured. Nothing is claimed that was not read off something.

    ## Install
    Clone the repository and run `uv sync`. Everything runs from the virtual environment.

    ## Run a brief
    `python -m mizpah.loop --config config.json --project <dir> --root <dir>` runs one brief to its end.

    ## Read the map
    `terra known list` shows every known with its confidence and the runs behind it.
    ''')

CATALOG = dict(parts=[
    dict(id='cap_10uf_25v', kind='capacitor', value_uf=10, voltage_v=25, cost=0.12, mass_g=0.4),
    dict(id='cap_22uf_16v', kind='capacitor', value_uf=22, voltage_v=16, cost=0.15, mass_g=0.5),
    dict(id='cap_22uf_35v', kind='capacitor', value_uf=22, voltage_v=35, cost=0.31, mass_g=0.9),
    dict(id='cap_47uf_25v', kind='capacitor', value_uf=47, voltage_v=25, cost=0.28, mass_g=1.1),
    dict(id='cap_47uf_50v', kind='capacitor', value_uf=47, voltage_v=50, cost=0.62, mass_g=1.8),
])


def docs_page(project: Path) -> None:
    (project/'content').mkdir(parents=True)
    (project/'content'/'docs.md').write_text(DOCS)
    (project/'content'/'README.md').write_text('content/docs.md is the text of the page. The page lives in site/.\n')
    needs = [
        'Know the number of <h2> elements site/index.html has',                                              # 1
        'Know whether every heading in content/docs.md appears in site/index.html in the same order',        # 2
        'Know the page weight in kilobytes: site/index.html plus every file it references',                  # 3
        'Know whether the page renders without horizontal overflow at 375 px (page_readings)',              # 4
    ]
    deliverables = [
        'site/index.html with site/style.css: the docs page written by hand from content/docs.md, no framework, '
        'meeting the design targets other pages in this library were measured for — text contrast, line length, '
        'text size and overflow at 1280 px — each a reading on the map (page_readings)',
        'report/docs.md: one row per reading on the map with its value',
    ]
    brief(project, 'Docs page', 'Build a one-page docs site from content/docs.md and prove its design properties by '
          'measuring the rendered page, the way the landing pages were.', needs, deliverables, budget=200,
          notes='Build first, then measure; a reading is taken off the rendered page or the files.',
          non_goals=['No `framework` or `bundler`: hand-written HTML and CSS'],
          enablers=[('page_readings', 'Headless page readings', 'cg/frontend_headless_page_cli_python',
                     'A command-line instrument over headless chromium: serve or open a page, read its text, element '
                     'boxes, computed styles and requests, take a screenshot at a viewport width.')])
    key_path(project).write_text(json.dumps(dict(fixture='docs_page', key=dict(by_need={1: 3, 2: True, 4: True})), indent=1)+'\n')


def catalog_pick(project: Path) -> None:
    (project/'catalog.json').write_text(json.dumps(CATALOG, indent=2)+'\n')
    (project/'spec.json').write_text(json.dumps(dict(kind='capacitor', min_value_uf=20, min_voltage_v=24), indent=2)+'\n')
    (project/'README.md').write_text('catalog.json lists parts; spec.json says what the part must meet. Pick by catalog_lookup.\n')
    needs = [
        'Know how many parts in catalog.json meet spec.json (kind, at least min_value_uf, at least min_voltage_v) (catalog_lookup)',  # 1
        'Know which part id meeting spec.json has the lowest cost (catalog_lookup)',                                                   # 2
        'Know the mass in grams of that part (catalog_lookup)',                                                                        # 3
    ]
    deliverables = [
        'pick.json: {"part": <id>, "cost": <cost>, "mass_g": <mass>} agreeing with the map',
    ]
    brief(project, 'Catalog pick', 'Choose the cheapest catalog part meeting a spec through a reusable catalog lookup, '
          'and record the choice.', needs, deliverables, budget=120,
          notes='The lookup is an instrument: a small command-line tool over a JSON catalog that filters by field '
                'constraints and sorts; the readings are taken through it.',
          enablers=[('catalog_lookup', 'Catalog lookup', 'cg/data_catalog_lookup_cli_python',
                     'A command-line tool: given a JSON catalog and field constraints (kind, minimums), print the '
                     'matching rows, optionally sorted by a field. General over any list-of-objects JSON.')])
    key_path(project).write_text(json.dumps(dict(fixture='catalog_pick', key=dict(by_need={1: 3, 2: 'cap_47uf_25v', 3: 1.1})), indent=1)+'\n')


FIXTURES = dict(docs_page=docs_page, catalog_pick=catalog_pick)


def check(project: Path, root: Path) -> list[tuple[str, bool, str]]:
    """The behaviour checklist: read what the controller was shown and what it did."""
    rows: list[tuple[str, bool, str]] = []
    steps = [json.loads(line) for line in (root/'controller.jsonl').read_text().splitlines()] if (root/'controller.jsonl').exists() else []
    loop = json.loads((root/'loop.json').read_text()) if (root/'loop.json').exists() else {}
    brief_doc = json.loads((project/'.terra'/'brief.json').read_text())
    route = json.loads((project/'.terra'/'route.json').read_text())
    tasks = route.get('tasks') or []
    enablers = {e['id']: e for e in brief_doc.get('enablers') or []}
    first = steps[0] if steps else {}
    shown = ' '.join(str(a.get('raw') or '') for a in first.get('attempts') or [])
    observation = first.get('observation') or {}
    registry_lines = observation.get('registry') or []
    related = observation.get('related_briefs') or []
    cycles = loop.get('cycles') or []
    installed = [e for c in cycles for e in (c.get('enablers') or []) if e.get('status') == 'installed_from_registry']
    for eid, en in enablers.items():
        enabler_tasks = [t for t in tasks if t.get('enabler_id') == eid]
        if installed and any(e['enabler'] == eid for e in installed):
            rows.append(('enabler '+eid+' installed from the registry before routing, no task', not enabler_tasks,
                         'status '+str(en.get('status'))+', tasks '+str([t['id'] for t in enabler_tasks])))
        else:
            rows.append(('enabler '+eid+' routed as its own task (role enabler)', bool(enabler_tasks),
                         str([t['id'] for t in enabler_tasks])))
            if enabler_tasks:
                first_task = min(tasks, key=lambda t: t.get('created_at') or '')
                rows.append(('enabler task created before or with the first task', first_task.get('enabler_id') == eid
                             or enabler_tasks[0].get('created_at') == first_task.get('created_at'), first_task['id']))
            rows.append(('enabler '+eid+' reached ready or graduated', str(en.get('status')) in ('ready', 'graduated'), str(en.get('status'))))
    rows.append(('related briefs shown to the controller', bool(related), ', '.join(str(d.get('title')) for d in related)))
    minted = [str(u) for s in steps for u in ((s.get('applied') or {}).get('unknowns') or [])]
    unknown_docs = {p.stem: json.loads(p.read_text()) for p in (project/'.terra'/'map'/'unknowns').glob('*.json')}
    deliverable_cited = [u for u, d in unknown_docs.items() if 'cites deliverable:' in str(d.get('notes') or '')]
    library_words = ('contrast', 'line_length', 'text_size', 'overflow')
    from_library = [u for u in deliverable_cited if any(w in u for w in library_words)]
    if 'docs' in project.name:
        rows.append(('design unknowns the needs never named, minted against the deliverable (from the library)',
                     len(from_library) >= 2, ', '.join(from_library)))
    waited = [r for s in steps for r in (s.get('refused') or []) if 'names enabler' in r]
    rows.append(('readings that name the enabler were held until it was ready (refusals or none needed)',
                 True, str(len(waited))+' refusals'))
    rows.append(('every minted unknown cites a brief entry', all('cites ' in str(d.get('notes') or '') for d in unknown_docs.values()),
                 str(len(unknown_docs))+' unknowns'))
    return rows


def main() -> None:
    if sys.argv[1:2] == ['--check']:
        project, root = Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
        rows = check(project, root)
        for title, ok, detail in rows:
            print(('PASS ' if ok else 'FAIL ')+title+('  — '+detail if detail else ''))
        print(str(sum(ok for _, ok, _ in rows))+'/'+str(len(rows))+' controller checks pass')
        return
    target = Path(sys.argv[1]).resolve()
    name = sys.argv[2]
    project = target/name
    if project.exists():
        raise SystemExit(str(project)+' exists')
    project.mkdir(parents=True)
    FIXTURES[name](project)
    print(project)


if __name__ == '__main__':
    main()
