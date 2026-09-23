"""A landing page for Mizpah: the design brief whose answer cannot be verified but whose method can.

Usage: python -m fixtures.landing <target_dir>   (from engine/mizpah; makes <target_dir>/landing)

Nothing here has a planted answer. The needs are levels the page has to reach; what to read off the
rendered page (headless chromium through the page CLI) or the files to show it is the loop's to work
out. The key keeps the first curve's targets and the evidence numbers for a scorer that wants them;
the y of a run is the map, the page as rendered, and what the library gained. Run with
the engine config (its sandbox gives chromium /sys, 3 GB, 256 processes) and the crew pinned on the project.
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
    1. Brief — needs, deliverables and non-goals, held by the requestor; agents can only propose changes.
    2. Unknown — the controller mints one typed question per thing the map still owes the brief.
    3. Work order — one task per group of unknowns, with a bucket for effort; it says what, never how.
    4. Procedure — the worker opens a method from the playbook and walks it one step at a time.
    5. Probe and run — an instrument reads the source; the run is stamped and linked to what it measured.
    6. Known — enough agreeing runs graduate a reading to a known with a confidence.
    7. Gate — a mechanical check over the whole map, no arguing. Green is when the library takes the method.

    ## The three tools
    - Terra — the map: unknowns, probes, runs, typed knowns, staleness, the gate.
    - Playbook — the methods: procedures a worker walks and improves when the gate is green.
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
      (the seven steps), the three tools, evidence (the numbers in evidence.json, each with what it
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
    'Headless chromium is in the sandbox; a browser must outlive one command, so it runs as a service: '
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
    (content/'README.md').write_text(textwrap.dedent('''\
        content/ is the source for the page: pitch.md (the words), brand.md (the constraints),
        evidence.json (the only numbers the evidence section may state). The page lives in site/;
        screenshots in site/shots/.
        '''))
    needs = [
        'The page is at the level of a well-set editorial page: a designer looking at it rendered at 1280 and at 375 px '
        'wide finds the type, spacing, colour and hierarchy deliberate and nothing to fix — prose at a readable measure, '
        'one accent, a spacing scale, a call to action that is seen first.',
        'The page is honest: every word carries the meaning of content/pitch.md, every constraint in content/brand.md '
        'holds, and the evidence section states only numbers content/evidence.json holds.',
        'The page is lean and accessible: it loads with nothing from outside site/, reads the same with scripts off, '
        'and passes what a publisher checks before shipping — text contrast, alt text, no horizontal overflow at phone '
        'width, the call to action above the fold.',
    ]
    deliverables = [
        'site/index.html with site/style.css: the landing page, HTML and CSS written by hand, sections hero, how it '
        'works, the three tools, evidence, get started, in that order',
        'site/shots/desktop-1280.png and site/shots/mobile-375.png: the final page rendered by headless chromium at '
        '1280 and 375 px wide',
    ]
    brief(project, 'Mizpah landing page',
          'Build the landing page for Mizpah from the words in content/ and make it a page a person would read and '
          'trust. In: content/ (pitch.md, brand.md, evidence.json), headless chromium and the page CLI in the library. '
          'Out: site/index.html, site/style.css, site/shots/desktop-1280.png, site/shots/mobile-375.png.',
          needs, deliverables, budget=120,
          notes=RENDER_NOTE,
          non_goals=['No `framework`, `bundler` or `build step`: the page is HTML and CSS written by hand.',
                     'No `external request`: no fonts, scripts or images from outside site/.',
                     'No `javascript` for layout or content: the page reads the same with scripts off.',
                     'No notes, plan, report or write-up files — the page is the deliverable.'],
          enablers=[('page_readings', 'Headless page readings', 'cg/frontend_headless_page_cli_python',
                     'A command-line instrument over headless chromium: serve or open a page, read its text, element boxes, '
                     'computed styles and requests, take a screenshot at a viewport width.')] if enablers else ())
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
