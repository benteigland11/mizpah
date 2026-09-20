"""The mizpah.ai brand: drills first, then the kit, then the site — a real long-horizon program.

Usage: python -m fixtures.brand <target_dir> logo_mark|palette|type_system|social_card|brand_kit   (makes <target_dir>/<name>)

Every brief here measures what can be measured (an SVG renders at 16 px, a text/background pair clears AA, a
scale ratio holds) and leaves taste to the person: `logo_mark` produces three candidates and a report, and the
person's pick is an input to `brand_kit`. The raw material — what the name means, what the product is, the
evidence the runs produced — is seeded under content/ so every claim on the site can be a reading.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import textwrap

from .suite import brief, key_path, phase

NAME = textwrap.dedent('''\
    # Mizpah — the name

    Mizpah (Hebrew: מִצְפָּה, "watchtower") is the heap of stones Jacob and Laban raised as a witness between them
    (Genesis 31:49): "The LORD watch between me and thee, when we are absent one from another." It names a place
    where two parties who cannot see each other's work agree on something that can be checked. It is also a
    keepsake tradition: a coin split in two, each half held by one party.

    That is the product: a loop where the answer cannot be verified but the method and the evidence can, where
    whoever writes the reference never grades against it, and where nothing enters the record that was not read
    off something. Witness, not trust.
    ''')

PRODUCT = textwrap.dedent('''\
    # Mizpah — the product, plainly

    Mizpah is a self-improving engineering loop. A brief says what is owed. A controller turns it into typed
    questions (unknowns). A worker answers each one by writing a probe that reads the source and records the
    reading on a map. A gate checks the map against the brief mechanically. Only after green may the worker write
    the method it followed into a shared playbook, so the next project starts from it.

    Three tools carry it: Terra (the map), Playbook (the methods), Cartograph (the instruments). Local models on
    one GPU run it unattended for hours; hosted models run it faster. The library they build is files: procedures,
    widgets, packed enablers — inspectable, diffable, shared between models.

    What it is for: work where "done" cannot be verified by looking at the answer — a sized battery pack, a
    landing page's design, a codebase survey, a proof — but the readings that support it can.
    ''')

VOICE = textwrap.dedent('''\
    # Voice and constraints

    Plain, engineering, specific. Numbers over adjectives. No "revolutionary", no "AI-powered", no exclamation
    marks. Short sentences. The reader is an engineer who distrusts claims; every claim on the site is a reading
    from a real run, and the source is named.

    Visual direction, as constraints not as a look: one accent colour, a dark and a light scheme that both clear
    WCAG AA for body text, a display face and a text face (or one family with two weights), a mark that survives
    16 px, and generous measure (60-75 characters). Nothing decorative that is not also informative.
    ''')

EVIDENCE = dict(
    sales_readings_correct=18, sales_readings_total=18, sales_deliverables_verified=5,
    landing1_targets_met=15, landing1_targets_total=15,
    ornith_hours_unattended=17, briefs_run_in_a_day=15, local_gpu_count=1,
    procedures_in_library=233, widgets_in_library=1639,
    models_that_share_the_library=2, fabricated_readings=0,
)


def _seed(project: Path) -> None:
    content = project/'content'
    content.mkdir(parents=True, exist_ok=True)
    (content/'name.md').write_text(NAME)
    (content/'product.md').write_text(PRODUCT)
    (content/'voice.md').write_text(VOICE)
    (content/'evidence.json').write_text(json.dumps(EVIDENCE, indent=2)+'\n')
    (content/'README.md').write_text('content/ is the source: name.md (what the name means), product.md (what it is), '
                                     'voice.md (how it speaks and the visual constraints), evidence.json (the only numbers '
                                     'a page may state). brand/ is where brand files go; site/ is the site.\n')


def logo_mark(project: Path) -> None:
    _seed(project)
    needs = [
        'Know the number of candidate marks under brand/marks/ (each a directory with mark.svg and a one-paragraph rationale.md)',  # 1
        'Know whether every mark.svg is valid SVG with a viewBox, no raster image, no external reference and no script',            # 2
        'Know the file size in bytes of the largest mark.svg',                                                                       # 3
        'Know whether every mark renders at 16 by 16 pixels with at least 20 percent of pixels inked (page_readings)',               # 4
        'Know the contrast ratio of every mark\'s fill against white and against near-black (#111), and the lowest of them',        # 5
        'Know whether every candidate has a monochrome variant mark-mono.svg drawn in one colour: every fill and stroke is that '
        'colour or none, and it is the same drawing as mark.svg recoloured (same path data)',                                     # 6
        'Know whether every candidate has a wordmark.svg that places its own mark.svg drawing beside the name "Mizpah" set as '
        'paths (letter outlines, no <text> element), so the three wordmarks differ in mark and in letterform',                   # 7
        'Know whether brand/marks/report.md states, per candidate, the readings above and the rationale, every number a reading on the map',  # 8
        'Know the number of distinct mark.svg drawings among the candidates: two count as the same when their path data, '
        'normalised (whitespace and numeric precision), is identical or one is a scaled or recoloured copy of the other',       # 9
        'Know the number of distinct wordmark.svg letterforms, three being the target: two count as the same when their letter '
        'path data, normalised, is identical; a <text> element counts as no letterform at all',                                  # 10
    ]
    deliverables = [
        'brand/marks/<candidate>/`mark.svg`, `mark-mono.svg`, `wordmark.svg` and `rationale.md` for exactly three candidates, each a '
        'different idea drawn from content/name.md (the witness heap, the split coin, the watchtower are starting points, not '
        'a list to copy), hand-written SVG, no raster, no external reference',
        '`brand/marks/report.md`: one section per candidate with its readings (needs 2 to 7) and rationale, and a closing table; '
        'the choice between candidates is not made here — a person makes it',
    ]
    brief(project, 'Logo mark candidates', 'Draw three candidate marks for Mizpah as hand-written SVG, prove each renders '
          'small, clears contrast and has mono and wordmark variants, and report the readings so a person can choose.',
          needs, deliverables, budget=300,
          notes='A mark is a file; its qualities are readings. Render through the page reader: open an HTML page that '
                'places the SVG at a size and read pixels or boxes off it. '+'Read content/voice.md before drawing.',
          non_goals=['No `raster` source: marks are vector, exports are readings not deliverables',
                     'No `choice` between candidates: the report shows, a person chooses',
                     'No `stock` icon or copied glyph: the marks are drawn here',
                     'No `copy` of one candidate into another: three candidates are three drawings (need 9 counts them)',
                     'No shared `wordmark`: each candidate\'s wordmark carries its own mark and its own letterforms (need 10 counts them)'],
          enablers=[('page_readings', 'Headless page readings', 'cg/frontend_headless_page_cli_python',
                     'A command-line instrument over headless chromium: serve or open a page, read its text, element boxes, '
                     'computed styles and requests, take a screenshot at a viewport width.')])
    key_path(project).write_text(json.dumps(dict(fixture='logo_mark', key=dict(by_need={1: 3, 2: True, 4: True, 6: True, 7: True, 8: True, 9: 3, 10: 3},
                                                                              targets={3: '<=20000', 5: '>=4.5'})), indent=1)+'\n')


def palette(project: Path) -> None:
    _seed(project)
    needs = [
        'Know the number of colour tokens brand/tokens.css declares as CSS custom properties on :root',                        # 1
        'Know the number of tokens that have a dark-scheme value under prefers-color-scheme: dark',                              # 2
        'Know the contrast ratio of body text against page background in the light scheme',                                     # 3
        'Know the contrast ratio of body text against page background in the dark scheme',                                      # 4
        'Know the contrast ratio of the accent colour against the light background and against the dark background, and the lowest',  # 5
        'Know the number of distinct hues (rounded to 15 degrees) the non-neutral tokens use',                                   # 6
        'Know whether every text/background pair brand/pairs.json names clears 4.5:1 in both schemes',                           # 7
        'Know whether brand/palette.md documents every token with its role, both values and the pairs it may be used in',        # 8
    ]
    deliverables = [
        '`brand/tokens.css`: the colour system as custom properties on :root with a dark scheme, named by role '
        '(--bg, --fg, --accent, --muted, --surface, --border and what else is needed), no colour literal anywhere but here',
        '`brand/pairs.json`: the text/background pairs the system allows, each with both schemes\' ratios',
        '`brand/palette.md`: the system explained, every number a reading on the map',
    ]
    brief(project, 'Colour system', 'Define the mizpah.ai colour tokens for light and dark, prove every allowed text/'
          'background pair clears AA, and document the system.', needs, deliverables, budget=250,
          notes='One accent (content/voice.md). Ratios are computed from the token values with the WCAG formula, '
                'through an instrument; the library holds a contrast widget.',
          non_goals=['No `second accent`: one accent colour', 'No `gradient` in the token set'],
          enablers=[('contrast_ratio', 'WCAG contrast ratio', 'cg/frontend_wcag_contrast_ratio_python',
                     'Compute the WCAG 2 contrast ratio between two colours (hex, rgb) from the command line or Python.')])
    key_path(project).write_text(json.dumps(dict(fixture='palette', key=dict(by_need={7: True, 8: True},
                                                                            targets={1: '>=6', 2: '>=6', 3: '>=7', 4: '>=7', 5: '>=4.5', 6: '<=2'})), indent=1)+'\n')


def type_system(project: Path) -> None:
    _seed(project)
    needs = [
        'Know the scale ratio between consecutive heading sizes in brand/type.css (the ratio of h1 to h2 and h2 to h3, rounded to 2 decimals)',  # 1
        'Know the number of font families brand/type.css names, and whether each is a system stack or a self-hosted file under brand/fonts/ with a licence file beside it',  # 2
        'Know the body text size in pixels as rendered on brand/type-sample.html at 1280 px (page_readings)',                  # 3
        'Know the body line height as a ratio to its font size as rendered (page_readings)',                                   # 4
        'Know the mean paragraph line length in characters as rendered at 1280 px (page_readings)',                            # 5
        'Know the mean paragraph line length in characters as rendered at 375 px (page_readings)',                             # 6
        'Know whether brand/type.md documents the scale, the families, the sizes at each step and the measures, every number a reading on the map',  # 7
    ]
    deliverables = [
        '`brand/type.css`: the type system as custom properties and element rules (h1 to h4, p, small, code), one display face '
        'and one text face or one family with two weights, sizes from a named ratio',
        '`brand/type-sample.html`: a page using brand/type.css and brand/tokens.css (copy it from the palette drill\'s result '
        'if present, else define minimal tokens) showing every level with real content from content/product.md',
        '`brand/type.md`: the system explained, every number a reading on the map',
    ]
    brief(project, 'Type system', 'Define the mizpah.ai type scale and faces, render a sample, and prove the measures.',
          needs, deliverables, budget=250, notes='Measure (60-75 characters) is the constraint that matters most (content/voice.md).',
          non_goals=['No `external font` request: a self-hosted file or a system stack', 'No `more than two` families'],
          enablers=[('page_readings', 'Headless page readings', 'cg/frontend_headless_page_cli_python',
                     'A command-line instrument over headless chromium: open a page, read boxes, computed styles, text.')])
    key_path(project).write_text(json.dumps(dict(fixture='type_system', key=dict(by_need={7: True},
                                                                                targets={1: '>=1.2', 3: '>=18', 4: '>=1.4', 5: '<=75', 6: '<=45'})), indent=1)+'\n')


def social_card(project: Path) -> None:
    _seed(project)
    needs = [
        'Know the rendered width and height in pixels of brand/social/card.html at its declared viewport (page_readings)',  # 1
        'Know whether brand/social/card.png exists and is 1200 by 630 pixels',                                               # 2
        'Know the contrast ratio of the card\'s title text against its background',                                          # 3
        'Know the number of external requests the card page makes',                                                          # 4
        'Know whether the card states the name and the one-line description from content/product.md verbatim',              # 5
    ]
    deliverables = [
        '`brand/social/card.html` with its CSS inline or in brand/social/card.css: the Open Graph card, 1200 by 630, using '
        'brand tokens and type if present under brand/, the name, the mark if present, and one line',
        '`brand/social/card.png`: the card rendered by headless chromium at exactly 1200 by 630',
    ]
    brief(project, 'Social card', 'Make the 1200 by 630 Open Graph card for mizpah.ai from the brand and prove its dimensions '
          'and contrast.', needs, deliverables, budget=150, notes='The PNG is a reading of the HTML; the HTML is the source.',
          non_goals=['No `external request`'],
          enablers=[('page_readings', 'Headless page readings', 'cg/frontend_headless_page_cli_python',
                     'A command-line instrument over headless chromium: open a page, screenshot at a viewport width.')])
    key_path(project).write_text(json.dumps(dict(fixture='social_card', key=dict(by_need={2: True, 4: 0, 5: True}, targets={3: '>=4.5'})), indent=1)+'\n')


def brand_kit(project: Path, *, chosen_mark: str = '') -> None:
    """The composed brief: takes the drills' results (copied in under brand/) and the person's chosen mark."""
    _seed(project)
    needs = [
        'Know whether brand/mark.svg, brand/mark-mono.svg and brand/wordmark.svg are the chosen candidate\'s files, unchanged from brand/marks/'+(chosen_mark or '<chosen>'),  # 1
        'Know whether brand/tokens.css and brand/type.css are present and every pair in brand/pairs.json still clears 4.5:1',   # 2
        'Know the number of favicon exports under brand/favicon/ (16, 32, 180, 512) and whether each has the declared size',  # 3
        'Know whether brand/brand.md documents the mark, its clear space (as a multiple of its height) and minimum size, the tokens, the type, the voice, and names every file',  # 4
        'Know whether a sample page brand/sample.html using only the kit renders with no external request and clears the pairs',  # 5
        'Know the total size in kilobytes of the kit (everything under brand/ except marks/)',                                 # 6
    ]
    deliverables = [
        'brand/: mark.svg, mark-mono.svg, wordmark.svg, tokens.css, type.css, favicon/ (16, 32, 180, 512 px PNG and favicon.ico), '
        'social/card.png, brand.md — the kit a site is built from',
        'brand/brand.md: the guide — mark usage (clear space, minimum size, on light and dark), colour roles, type scale, '
        'voice — every number a reading on the map',
        'brand/sample.html: one page using only the kit, as the proof',
    ]
    brief(project, 'Brand kit', 'Compose the chosen mark, the colour system and the type system into the mizpah.ai brand '
          'kit with a guide and a proof page.', needs, deliverables, budget=300,
          notes='The drills\' outputs are under brand/marks/, brand/tokens.css, brand/type.css already; the chosen mark is '
                +(chosen_mark or '<set by the person>')+'. Compose; do not redraw.',
          non_goals=['No `redraw` of the mark or `recolour` of the tokens: the drills decided those, this composes'],
          enablers=[('page_readings', 'Headless page readings', 'cg/frontend_headless_page_cli_python', 'Headless chromium readings and screenshots.'),
                    ('contrast_ratio', 'WCAG contrast ratio', 'cg/frontend_wcag_contrast_ratio_python', 'WCAG 2 contrast ratio between two colours.')])
    phase(project, 'compose', 'Assemble the kit files', needs='1-3', deliverables='1', points=150)
    phase(project, 'prove', 'Guide and proof page', needs='4-6', deliverables='2-3', points=120)
    key_path(project).write_text(json.dumps(dict(fixture='brand_kit', key=dict(by_need={1: True, 2: True, 3: 4, 4: True, 5: True}, targets={6: '<=400'})), indent=1)+'\n')


FACTS = [
    'a brief says what is owed', 'unknowns are typed questions', 'a worker answers by a probe that reads the source',
    'readings go on a map', 'a gate checks the map against the brief mechanically', 'methods are written to a playbook only after green',
    'the next project starts from the playbook', 'local models on one GPU run it unattended', 'nothing is claimed that was not read off something',
]

BANNED = ['revolutionary', 'ai-powered', 'seamless', 'cutting-edge', 'game-changing', 'unlock', 'supercharge', 'effortless',
          'next-generation', 'best-in-class', 'leverage', 'empower', '!']


def _seed_copy(project: Path) -> None:
    _seed(project)
    (project/'content'/'facts.json').write_text(json.dumps(FACTS, indent=2)+'\n')
    (project/'content'/'banned.json').write_text(json.dumps(BANNED, indent=2)+'\n')
    (project/'content'/'README.md').write_text((project/'content'/'README.md').read_text()+
        'facts.json: the facts a piece of copy must carry (coverage is counted against it). banned.json: words and marks '
        'that may not appear. Every sentence of copy traces to a line of product.md or name.md; every number to evidence.json.\n')


def headline(project: Path) -> None:
    _seed_copy(project)
    needs = [
        'Know the number of candidates in copy/headlines.json (each a headline and a subhead)',                                    # 1
        'Know the length in characters of the longest headline and of the longest subhead',                                        # 2
        'Know the number of banned words or marks (content/banned.json) across all candidates',                                     # 3
        'Know, for each candidate, how many of the facts in content/facts.json its headline plus subhead carry, and the best count',  # 4
        'Know whether every number any candidate states appears as a value in content/evidence.json',                               # 5
        'Know whether every candidate names the product ("Mizpah") in the headline or the subhead',                                 # 6
        'Know whether copy/headlines.md shows every candidate with its readings and a one-line note on what it leads with',        # 7
    ]
    deliverables = [
        '`copy/headlines.json`: ten candidates, each {"headline": ..., "subhead": ..., "leads_with": ...}, headlines at most 60 '
        'characters, subheads at most 140, from content/product.md and content/name.md, no banned word',
        '`copy/headlines.md`: the candidates with their readings (needs 2 to 6) and a closing table; the choice is a person\'s',
    ]
    brief(project, 'Headline candidates', 'Write ten headline and subhead pairs for mizpah.ai that carry the most of what '
          'matters in the fewest words, invent nothing, and report the readings so a person can choose.', needs, deliverables,
          budget=200, notes='Coverage is counted by fact: a fact is carried when its content words appear (stemmed) in the '
                            'candidate. Fewer words at equal coverage is better; say so in the report.',
          non_goals=['No `choice` between candidates', 'No `claim` that is not in content/ (numbers only from evidence.json)'])
    key_path(project).write_text(json.dumps(dict(fixture='headline', key=dict(by_need={1: 10, 3: 0, 5: True, 6: True, 7: True},
                                                                             targets={2: '<=140', 4: '>=3'})), indent=1)+'\n')


def pitch_copy(project: Path) -> None:
    _seed_copy(project)
    needs = [
        'Know the word count of copy/home.md',                                                                                   # 1
        'Know how many of the facts in content/facts.json copy/home.md carries',                                                 # 2
        'Know the number of sentences in copy/home.md that trace to no line of content/product.md or content/name.md (an invention)',  # 3
        'Know whether every number copy/home.md states appears as a value in content/evidence.json, each with its source named',  # 4
        'Know the number of banned words or marks in copy/home.md',                                                              # 5
        'Know the mean sentence length in words and the longest sentence',                                                       # 6
        'Know whether copy/home.md has, in order, sections for what it is, how it works, the evidence, and how to start',         # 7
        'Know whether the first sentence of copy/home.md names Mizpah and says what it is',                                      # 8
        'Know whether copy/home.md documents its readings in copy/home-readings.md, every number a reading on the map',          # 9
    ]
    deliverables = [
        '`copy/home.md`: the home page copy — under 350 words, four sections in order (what it is, how it works, the evidence, '
        'how to start), every fact from content/facts.json carried, every number from content/evidence.json with its source, '
        'no banned word, no sentence that is not traceable to content/',
        '`copy/home-readings.md`: the readings (needs 1 to 8) as a table',
    ]
    brief(project, 'Home page copy', 'Write the mizpah.ai home page copy that carries every fact that matters in under 350 '
          'words with nothing invented, and prove it by readings.', needs, deliverables, budget=250,
          notes='Tracing: a sentence traces when at least half its content words (stemmed) appear in one line of product.md or '
                'name.md; the readings name the line. Plain, specific, short (content/voice.md).',
          non_goals=['No `claim` outside content/', 'No `adjective` in place of a number where evidence.json has one'])
    key_path(project).write_text(json.dumps(dict(fixture='pitch_copy', key=dict(by_need={2: 9, 3: 0, 4: True, 5: 0, 7: True, 8: True, 9: True},
                                                                               targets={1: '<=350', 6: '<=20'})), indent=1)+'\n')


FIXTURES = dict(logo_mark=logo_mark, palette=palette, type_system=type_system, social_card=social_card, brand_kit=brand_kit,
                headline=headline, pitch_copy=pitch_copy)


def main() -> None:
    target = Path(sys.argv[1]).resolve()
    name = sys.argv[2]
    project = target/name
    if project.exists():
        raise SystemExit(str(project)+' exists')
    project.mkdir(parents=True)
    extra = dict(chosen_mark=sys.argv[3]) if name == 'brand_kit' and len(sys.argv) > 3 else {}
    FIXTURES[name](project, **extra)
    print(project)


if __name__ == '__main__':
    main()
