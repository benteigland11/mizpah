"""Five small design briefs, each one thing a landing page needs done well, each leaving an instrument and a
method in the library for the next landing page.

Usage: python -m fixtures.design <target_dir> [names...]   (from engine/mizpah; makes <target_dir>/<name>)

  layout      a single column with a container that holds every section's prose, gutters at every width
  type        left-aligned prose, centered only where it should be, a heading scale, a measure that fits
  rhythm      the spacing scale applied: vertical gaps that sit on the scale, not just a short list of values
  components  a call to action with a real box and states; an ordered list with one marker per item
  tonality    surface layers and one accent; contrast across every text/background pair, not only the body

Each project holds a small page with the defect planted (the landing1 run showed each of them), the brand
notes, and needs that are readings off the rendered page. There is no planted truth: the key carries targets
(a property met or missed) exactly as the landing fixture does. Run with config.landing.json; the brief says
to run chromium as a service and the library holds the page CLI.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import textwrap

from .suite import brief, key_path

BRAND = textwrap.dedent('''\
    # Brand notes
    - Dark background, light text, exactly one accent color. System font stack only, no external requests.
    - Single column; prose measures around 70 characters; generous, regular vertical rhythm.
    - Tone: quiet, precise, engineering.
    ''')

RENDER_NOTE = (
    'A browser must outlive one command, so start it as a service: `svc start browser -- chromium-browser '
    '--headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage --user-data-dir=/work/.chrome '
    '--remote-debugging-port=9222 --remote-allow-origins=* about:blank`. The widget library has a page CLI over its '
    'DevTools port (search it): open file:///work/site/index.html, read text and the accessibility tree, screenshot '
    'at a viewport, evaluate JavaScript, take layout readings. A reading is taken off the rendered page, never estimated; '
    'an instrument that reads a design property is a widget, and the way to fix the property is a procedure.'
)

CSS_BASE = textwrap.dedent('''\
    :root { --bg: #141414; --panel: #1c1c1c; --fg: #e8e8e8; --muted: #9a9a9a; --accent: #e07a5f;
            --sp-1: 0.5rem; --sp-2: 1rem; --sp-3: 1.5rem; --sp-4: 2rem; --sp-5: 3rem; }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--fg);
           font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
           font-size: 18px; line-height: 1.6; }
    ''')

PROSE = ('The loop answers a question the only way allowed: a probe reads the source, a run is stamped, and the '
         'reading is recorded on the map with the runs behind it. A number without a run does not exist.')


DESIGN_NON_GOALS = ['No `framework`, `bundler` or `build step`: the page is HTML and CSS written by hand.',
                    'No `external request`: no fonts, scripts or images from outside site/.',
                    'No notes, plan or write-up files — the page is the deliverable.']


def _page(project: Path, body: str, css: str, title: str = 'Sample') -> None:
    site = project/'site'
    site.mkdir(parents=True)
    (site/'index.html').write_text('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
                                   '<meta name="viewport" content="width=device-width, initial-scale=1">'
                                   '<title>'+title+'</title><link rel="stylesheet" href="style.css"></head>\n<body>\n'
                                   +body+'</body></html>\n')
    (site/'style.css').write_text(CSS_BASE+css)
    (project/'content').mkdir()
    (project/'content'/'brand.md').write_text(BRAND)


def _key(project: Path, name: str, targets: dict[str, object]) -> None:
    (project/'content'/'targets.json').write_text(json.dumps({'need:'+k: v for k, v in targets.items()}, indent=2)+'\n')
    key_path(project).write_text(json.dumps(dict(fixture='design-'+name, key=dict(by_need={}, targets=targets)), indent=1)+'\n')


# ---------------------------------------------------------------- layout: the container that holds everything

def layout(project: Path) -> None:
    body = ('<section class="hero"><div class="wrap"><h1>A column that holds</h1><p>'+PROSE+'</p></div></section>\n'
            '<section><h2>Without the wrap</h2><p>'+PROSE+' '+PROSE+'</p></section>\n'
            '<section><div class="wrap"><h2>With the wrap</h2><p>'+PROSE+'</p></div></section>\n')
    # The defect: .wrap has no max-width, and the second section has no wrap at all; nothing bounds the prose.
    css = '.wrap { margin: 0 auto; padding: 0 var(--sp-2); }\nsection { padding: var(--sp-4) 0; }\n'
    _page(project, body, css, 'Layout')
    needs = [
        "The prose sits in one column a reader can follow: every section's text bounded to a comfortable measure by the same container, not by chance.",
        'The column survives a phone: a side gutter holds at any width, and nothing runs to the edge of the glass.',
        'It is centred and stays centred — a designer opening it at two widths would not reach for the CSS.',
    ]
    deliverables = [
        "site/style.css and site/index.html: one container class that bounds every section's prose, applied where a section lacks it",
    ]
    brief(project, 'Layout: one column that holds', 'Make the page a single bounded column: every section\'s prose '
          'inside one container with a measure, gutters that survive a phone width, and prove it by measuring the '
          'rendered page before and after.', needs, deliverables, budget=150, notes=RENDER_NOTE, non_goals=DESIGN_NON_GOALS)
    _key(project, 'layout', {'2': '<=80', '3': True, '4': '>=16', '5': '>=16', '6': True})


# ---------------------------------------------------------------- type: alignment and scale

def type_(project: Path) -> None:
    body = ('<section class="hero"><div class="wrap"><h1>Centred where it should be</h1><p class="lede">'+PROSE+'</p>'
            '</div></section>\n<section><div class="wrap"><h2>Left where it should be</h2><p>'+PROSE+'</p><p>'+PROSE+'</p>'
            '<h3>A smaller heading</h3><p>'+PROSE+'</p></div></section>\n')
    # The defect: everything centred, headings all the same size, the h1 barely larger than body text.
    css = ('.wrap { max-width: 70ch; margin: 0 auto; padding: 0 var(--sp-2); text-align: center; }\n'
           'h1, h2, h3 { font-size: 20px; font-weight: 700; }\nsection { padding: var(--sp-4) 0; }\n')
    _page(project, body, css, 'Type')
    needs = [
        'The type has one scale a reader can feel: sizes that step, headings that lead, nothing set by accident.',
        'The alignment says what it means: what is centred is centred on purpose, and running prose is not.',
        "The line length is readable at both widths, and the page's faces are the ones the brand names.",
    ]
    deliverables = [
        'site/style.css and site/index.html: the scale and alignment applied across the page',
    ]
    brief(project, 'Type: alignment and scale', 'Give the page a typographic scale and the right alignment per block, '
          'and prove it by measuring the rendered page before and after.', needs, deliverables, budget=150, notes=RENDER_NOTE, non_goals=DESIGN_NON_GOALS)
    _key(project, 'type', {'1': 0, '2': True, '3': '>=36', '4': '>=2', '5': '>=1.2', '6': '45..90'})


# ---------------------------------------------------------------- rhythm: the scale applied, not counted

def rhythm(project: Path) -> None:
    blocks = ''.join('<section><div class="wrap"><h2>Section '+str(i)+'</h2><p>'+PROSE+'</p><p>'+PROSE+'</p></div></section>\n'
                     for i in range(1, 5))
    body = '<section class="hero"><div class="wrap"><h1>Rhythm</h1><p>'+PROSE+'</p></div></section>\n'+blocks
    # The defect: a spacing scale is declared, but the gaps are ad hoc pixel values that ignore it.
    css = ('.wrap { max-width: 70ch; margin: 0 auto; padding: 0 var(--sp-2); }\n'
           'section { padding: 37px 0 11px; }\nh1 { margin: 0 0 9px; }\nh2 { margin: 23px 0 5px; }\np { margin: 0 0 13px; }\n')
    _page(project, body, css, 'Rhythm')
    needs = [
        'The vertical space comes from one spacing scale, not from a scatter of values a reader would feel as noise.',
        'The rhythm reads as deliberate: space between sections larger than space inside them, headings closer to what they head than to what came before.',
        'It holds at both widths — the page breathes the same way on a phone as on a desk.',
    ]
    deliverables = [
        "site/style.css: the spacing scale applied as the page's vertical rhythm",
    ]
    brief(project, 'Rhythm: the scale applied', 'Make the vertical rhythm come from the spacing scale, and prove it by '
          'measuring the gaps on the rendered page before and after.', needs, deliverables, budget=150, notes=RENDER_NOTE, non_goals=DESIGN_NON_GOALS)
    _key(project, 'rhythm', {'1': '<=6', '2': '>=0.9', '3': '>=48', '4': '<=8', '5': True})


# ---------------------------------------------------------------- components: a CTA and a list

def components(project: Path) -> None:
    steps = ''.join('<li><span class="n">'+str(i)+'</span> Step '+str(i)+' — '+PROSE[:60]+'.</li>' for i in range(1, 6))
    body = ('<section class="hero"><div class="wrap"><h1>Components</h1><p>'+PROSE+'</p>'
            '<a class="cta" href="#start">Get started</a></div></section>\n'
            '<section><div class="wrap"><h2>Steps</h2><ol class="steps">'+steps+'</ol></div></section>\n')
    # The defects: the CTA is inline text with 2px padding and no states; the list shows the browser marker AND a number span.
    css = ('.wrap { max-width: 70ch; margin: 0 auto; padding: 0 var(--sp-2); }\nsection { padding: var(--sp-4) 0; }\n'
           '.cta { background: var(--accent); color: #000; padding: 2px 4px; text-decoration: none; }\n'
           '.steps .n { font-weight: 700; margin-right: 4px; }\n')
    _page(project, body, css, 'Components')
    needs = [
        'The call to action is a button a thumb can hit and an eye can find, not a link pretending to be one.',
        'The steps read as a list: one item per step, marked once, numbered by the list itself.',
        "Both hold their shape at a phone width and against the page's own colours.",
    ]
    deliverables = [
        'site/style.css and site/index.html: the call to action as a button and the steps as a list',
    ]
    brief(project, 'Components: a button and a list', 'Make the call to action a button and the steps list a list, '
          'and prove both by measuring the rendered page before and after.', needs, deliverables, budget=150, notes=RENDER_NOTE, non_goals=DESIGN_NON_GOALS)
    _key(project, 'components', {'1': '>=44', '2': '>=16', '3': True, '4': '>=4.5', '5': 1, '6': True})


# ---------------------------------------------------------------- tonality: layers and one accent

def tonality(project: Path) -> None:
    body = ('<section class="hero"><div class="wrap"><h1>Tonality</h1><p>'+PROSE+'</p><a class="cta" href="#">Start</a></div></section>\n'
            '<section class="panel"><div class="wrap"><h2>On a panel</h2><p class="muted">'+PROSE+'</p>'
            '<p><a href="#">An inline link</a> in prose.</p></div></section>\n'
            '<section><div class="wrap"><h2>Evidence</h2><p class="muted">'+PROSE+'</p><span class="tag">note</span></div></section>\n')
    # The defects: muted text fails contrast on the panel, two accents (links use a second hue), the tag is accent-on-accent.
    css = ('.wrap { max-width: 70ch; margin: 0 auto; padding: 0 var(--sp-2); }\nsection { padding: var(--sp-4) 0; }\n'
           '.panel { background: #2a2a2a; }\n.muted { color: #6e6e6e; }\na { color: #4fa3ff; }\n'
           '.cta { background: var(--accent); color: #fff; padding: var(--sp-1) var(--sp-3); text-decoration: none; }\n'
           '.tag { background: var(--accent); color: #f0a080; padding: 2px 6px; }\n')
    _page(project, body, css, 'Tonality')
    needs = [
        'The page has a tonal system: a surface, layers that sit on it, and one accent that means one thing.',
        'Text is legible on every surface it lands on — a reader with ordinary eyes never has to lean in.',
        'The accent is spent where attention is wanted and nowhere else.',
    ]
    deliverables = [
        'site/style.css: the surfaces, layers and single accent applied across the page',
    ]
    brief(project, 'Tonality: layers and one accent', 'Give the page a tonal system — surface layers, one accent, '
          'contrast everywhere — and prove it by measuring every pair on the rendered page before and after.',
          needs, deliverables, budget=150, notes=RENDER_NOTE, non_goals=DESIGN_NON_GOALS)
    _key(project, 'tonality', {'1': '>=4.5', '2': 0, '3': '<=1', '4': '>=4.5', '5': '<=3', '6': True})


FIXTURES = dict(layout=layout, type=type_, rhythm=rhythm, components=components, tonality=tonality)


def main() -> None:
    target = Path(sys.argv[1]).resolve()
    names = sys.argv[2:] or list(FIXTURES)
    for name in names:
        project = target/name
        if project.exists():
            raise SystemExit(str(project)+' exists')
        project.mkdir(parents=True)
        FIXTURES[name](project)
        print(project)


if __name__ == '__main__':
    main()
