"""A landing page for Mizpah: the design brief whose answer cannot be verified but whose method can.

Usage: python -m fixtures.landing <target_dir>   (from engine/mizpah; makes <target_dir>/landing)

Nothing here has a planted answer. Every need is a property of the page the worker builds, read off
the rendered page by headless chromium (a probe injects a measuring script and reads the DOM back) or
off the files; the report states those readings and whether each meets the target in
content/targets.json. The scorer checks targets, traceability and that the evidence section states
only numbers content/evidence.json holds. Run with config.landing.json (chromium needs /sys, 3 GB,
256 processes in the sandbox).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import textwrap

from .suite import brief, key_path

PITCH = textwrap.dedent('''\
    # Mizpah

    Tagline candidates (pick one, or write a better one that says the same thing):
    - The answer can't be verified. The method can.
    - An engineering loop that measures instead of guessing.
    - Small models, honest maps.

    ## What it is
    Mizpah is a self-improving engineering loop for work where the answer cannot be checked but the
    method and the evidence can. You write a brief: what you need to know, what must be delivered.
    A controller turns the brief into typed questions (unknowns). A worker answers each one the only
    way allowed: by writing a probe that reads the source, running it, and recording the reading on a
    map. A mechanical gate says whether the map answers the brief. Nothing on the map is typed in by
    hand; a number without a run behind it does not exist.

    ## How it works (the loop)
    1. Brief — needs and deliverables, held by the person; agents can only propose changes to it.
    2. Unknown — the controller mints one typed question per thing the map still owes the brief.
    3. Route — one task per group of unknowns; a task says what, never how.
    4. Procedure — the worker opens a method from the playbook and follows it step by step.
    5. Probe and run — a small script reads the source; the run is stamped and linked.
    6. Known — three agreeing runs graduate the reading to a known with a confidence.
    7. Gate — pass/fail over brief and map, no arguing. Green is the only time a worker may improve
       the playbook.
    8. Project eval — the controller judges map against brief: new unknowns, or a proposal that the
       brief itself is wrong.

    ## The three tools
    - Terra — the map: unknowns, probes, runs, typed knowns, staleness, the gate.
    - Playbook — the methods: procedures a worker opens as a checklist and improves after green.
    - Cartograph — the instruments: widgets a probe calls; found by search before anything is built.

    ## Why it matters
    Small local models drift, guess and please. Mizpah does not ask them to be honest; it makes
    dishonesty structurally impossible to record. When the data cannot answer a need, the loop says
    so and proposes what data would — it never invents.

    ## Who it is for
    Anyone running an agent on their own hardware who wants the output to be checkable: analysts,
    engineers, researchers, small teams.

    ## Get started
    - Read the design: docs/DESIGN.md in the repository
    - Try the weather brief: one command, eighteen readings, every one verified
    - It runs on a single consumer GPU
    ''')

BRAND = textwrap.dedent('''\
    # Brand notes
    - Name: Mizpah. Do not add a logo image; a wordmark in type is enough.
    - Tone: quiet, precise, engineering. No exclamation marks, no "revolutionary".
    - Theme: dark background, light text, exactly one accent color used for the call to action and
      small emphasis. Light-theme support is not required.
    - Type: system font stack only (no webfonts, no external requests of any kind).
    - Layout: single column, max width around 70 characters for prose, generous vertical rhythm.
    - Sections, in order: hero (tagline, one sentence, one primary call to action), how it works
      (the eight steps), the three tools, evidence (the numbers in evidence.json, each with what it
      means), get started.
    - The evidence section states only numbers that appear in content/evidence.json. Nothing else.
    ''')

EVIDENCE = {
    'models_run': 3,
    'models_note': 'Gemma-4-26B, Bonsai-2-27B, Ornith-1.5-35B, all local, one GPU',
    'weather_readings_correct': 18, 'weather_readings_total': 18,
    'weather_note': 'the same brief on two different models, every planted answer right',
    'sales_readings_correct': 18, 'sales_readings_total': 18,
    'sales_deliverables': 5, 'sales_minutes': 71, 'sales_turns': 574,
    'sales_note': 'a brief the library had never seen: 18 readings, a CLI, tests, a README and a report',
    'unanswerable_needs': 3, 'fabricated_answers': 0,
    'honesty_note': 'three needs the data could not answer became three proposals, never a number',
    'widgets_reused_without_creation': 3,
    'reuse_note': 'instruments written for the weather brief carried a sales ledger unchanged',
}

TARGETS = {
    '3': '>=4.5', '4': '>=4.5', '5': False, '6': False, '7': '<=150', '8': '<=2', '9': '<=8', '10': True,
    '11': 0, '12': '>=16', '13': '45..90', '14': '<=8', '15': True, '16': 0,
}

RENDER_NOTE = (
    'The probe environment has headless chromium; a browser must outlive one command, so start it as a service: '
    '`svc start browser -- chromium-browser --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage '
    '--user-data-dir=/work/.chrome --remote-debugging-port=9222 --remote-allow-origins=* about:blank`. The widget '
    'library has a page CLI over its DevTools port (search it) that opens a URL such as file:///work/site/index.html, '
    'reads text and the accessibility tree, screenshots at a viewport, evaluates JavaScript and takes layout readings.'
)


def landing(project: Path, *, enablers: bool = False) -> None:
    content = project/'content'
    content.mkdir(parents=True)
    (content/'pitch.md').write_text(PITCH)
    (content/'brand.md').write_text(BRAND)
    (content/'evidence.json').write_text(json.dumps(EVIDENCE, indent=2)+'\n')
    (content/'targets.json').write_text(json.dumps({'need:'+k: v for k, v in TARGETS.items()}, indent=2)+'\n')
    (content/'README.md').write_text(textwrap.dedent('''\
        content/ is the source for the page: pitch.md (the words), brand.md (the constraints),
        evidence.json (the only numbers the evidence section may state), targets.json (what each
        measured property should meet, by brief need). The page lives in site/; screenshots in
        site/shots/; the design report in report/design.md.
        '''))
    needs = [
        'Know the number of <section> elements site/index.html has',                                              # 1
        'Know whether every section of the page opens with a heading (an h1 in the hero, an h2 elsewhere)',       # 2
        'Know the contrast ratio of body paragraph text against its background as rendered at 1280 px (page_readings)',  # 3
        "Know the contrast ratio of the primary call to action's text against its button background",             # 4
        'Know whether the page overflows horizontally when rendered 375 px wide (page_readings)',                 # 5
        'Know whether the page overflows horizontally when rendered 1280 px wide',                                # 6
        'Know the page weight in kilobytes: site/index.html plus every file it references',                       # 7
        'Know the number of distinct font families the rendered page uses',                                       # 8
        'Know the number of distinct margin and padding values site/style.css uses (its spacing scale)',          # 9
        'Know whether the primary call to action is visible above the fold at 1280 by 800 (page_readings)',       # 10
        'Know the number of <img> elements without alt text',                                                     # 11
        'Know the body paragraph text size in pixels as rendered',                                                # 12
        'Know the mean paragraph line length in characters as rendered at 1280 px (page_readings)',              # 13
        'Know the number of distinct colors site/style.css declares',                                             # 14
        'Know whether every number the evidence section states appears as a value in content/evidence.json',     # 15
        'Know the number of requests the page makes to any URL outside site/ (external fonts, scripts, images)',  # 16
    ]
    deliverables = [
        'site/index.html with site/style.css: one landing page written by hand in HTML and CSS, no framework, no '
        'external requests, with the sections hero, how it works, the three tools, evidence and get started in that '
        'order, its words from content/pitch.md and its constraints from content/brand.md',
        'site/shots/desktop-1280.png and site/shots/mobile-375.png: screenshots of the final page rendered by '
        'headless chromium at 1280 and 375 px wide (page_readings)',
        'report/design.md: one row per measured property (brief needs 1 to 16) stating the reading on the map and '
        'whether it meets the target in content/targets.json, with a closing line counting the targets met',
    ]
    brief(project, 'Mizpah landing page', 'Build a single landing page for Mizpah from the words in content/, and '
          'prove its design properties the way the loop proves anything: by measuring the rendered page.',
          needs, deliverables, budget=400,
          notes='Build the page first, then measure it; a reading is taken off the rendered page or the files, never '
                'estimated. '+RENDER_NOTE,
          non_goals=['No `framework`, `bundler` or `build step`: the page is HTML and CSS written by hand',
                     'No `external request`: no fonts, scripts or images from outside site/',
                     'No `javascript` for layout or content: the page reads the same with scripts off'],
          enablers=[('page_readings', 'Headless page readings', 'cg/frontend-headless-page-cli-python',
                     'A command-line instrument over headless chromium: serve or open a page, read its text, element boxes, '
                     'computed styles and requests, take a screenshot at a viewport width. The readings that name it are '
                     'taken through it.')] if enablers else ())
    key_path(project).write_text(json.dumps(dict(fixture='landing', key=dict(
        by_need={}, targets=TARGETS, evidence_numbers=[v for v in EVIDENCE.values() if isinstance(v, (int, float))],
        evidence_section='evidence')), indent=1)+'\n')


def main() -> None:
    target = Path(sys.argv[1]).resolve()
    project = target/'landing'
    if project.exists():
        raise SystemExit(str(project)+' exists')
    project.mkdir(parents=True)
    landing(project, enablers='--enablers' in sys.argv)
    print(project)


if __name__ == '__main__':
    main()
