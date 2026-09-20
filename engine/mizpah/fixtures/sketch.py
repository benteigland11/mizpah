"""Pencil sketches: one benchmark brief and a ladder of practice gyms on the `sketch` base.

Usage: python -m fixtures.sketch make benchmark [attempt]|value_scale|perspective_house|composition|texture
       python -m fixtures.sketch score <project_dir>            readings off the sketch the gym produced

A sketch, to the loop, is strokes: `sketch.svg` of paths with a width and a grey, drawn in an order recorded in
`strokes.json`, rendered to `sketch.png`. Graphite is emulated — hatching for value, contours for form,
construction lines underneath — so every skill is a reading, and "peaceful" stays the person's. The regime is
the piano's: the benchmark (a peaceful home in the countryside among rolling hills) runs cold, then after each
block of practice, its text pinned after the first run; the practice gyms each train one skill — value by
hatching, two-point perspective, composition, texture and line quality.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from mizpah import init as init_module  # noqa: E402
from mizpah import layout  # noqa: E402

TERRA = Path(__file__).resolve().parents[3]/'.venv'/'bin'/'terra'
BASE = 'sketch'
PINNED = Path.home()/'mizpah-runs'/'sketch'/'benchmark.brief.json'

STROKES = ('The sketch is strokes: `sketch.svg` holds only `<path>` elements (stroke, no fill except one paper rectangle), '
           'every stroke grey (`#000`..`#fff` or grey rgb), on an A4-proportion canvas (width:height = 1:1.414, landscape '
           'or portrait), rendered to `sketch.png` at least 2000 px on its long side with cairosvg.')
ORDER = ('`strokes.json` records every stroke in drawing order with its role — `construction`, `contour` or `hatching` — '
         'its width and its grey; construction strokes come first, contours next, hatching last, and the count equals the '
         'number of paths in sketch.svg, so a video of the sketch being drawn can be made from it.')
LINE = ('Line quality: stroke widths fall into at least three distinct classes; at least 60% of strokes are between 5 and '
        '150 px long at the rendered size (no single path draws a region); within any hatched region the hatching strokes '
        'share a direction within 15 degrees.')
NON_GOALS = ['No image-generation model, no photograph and no raster filter: every mark is a stroke the worker wrote.',
             'Randomness only seeded and stated in plan.md (hatching jitter is fine; a different seed is a different sketch).',
             'Greyscale only: no colour anywhere in the SVG.']


def terra(project: Path, *args: str) -> None:
    env = dict(os.environ, TERRA_DIRNAME=layout.STATE_DIRNAME)
    proc = subprocess.run([str(TERRA), *args], cwd=project, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise SystemExit('terra '+' '.join(args)+' failed: '+(proc.stderr or proc.stdout)[-600:])


def gym(title: str, mission: str, needs: list[str], deliverables: list[str], budget: int, *,
        non_goals: list[str] = ()) -> Path:
    folder = init_module.new_gym(title)
    init_module.init(folder, title=title, mission=mission, terra=str(TERRA), base=BASE)
    args = ['brief', 'set', '--status', 'active', '--budget-points', str(budget)]
    for need in needs:
        args += ['--need', need]
    for d in deliverables:
        args += ['--deliverable', d]
    for n in non_goals:
        args += ['--non-goal', n]
    terra(folder, *args)
    return folder


def benchmark() -> Path:
    return gym(
        'Peaceful home in the countryside',
        'Draw a pencil sketch of a peaceful home in the countryside among rolling hills: a drawing a person would '
        'take for graphite on paper, built from strokes, with a composition, perspective and values that hold up.',
        needs=[
            STROKES,
            'The subject is there and composed: a house with walls, a roof, a door and at least two windows; rolling '
            'hills as at least two hill contour lines that each cross the full width; a horizon in the upper half of '
            'the sheet placed on a third (between 28% and 40% from the top); the house\'s stroke mass centred inside '
            'the central thirds region horizontally.',
            'Perspective: the house is drawn in two-point perspective; extended lines of its parallel horizontal edges '
            'converge, each set to a point within 5% of the sheet width of a single vanishing point, both vanishing '
            'points on the horizon within 3% of the sheet height; vertical edges vertical within 2 degrees.',
            'Value: the rendered sketch has at least five distinct tonal bands made by hatching (grey levels between '
            '10% and 90%, each band covering at least 2% of the sheet), the sky the lightest region, the darkest values '
            'at or beside the house (the focal contrast), foreground darker than the far hills.',
            LINE, ORDER,
        ],
        deliverables=['sketch.svg', 'sketch.png', 'strokes.json',
                      'plan.md: the thumbnail composition (horizon, focal point, eye path), light direction, the value plan by region, the seed'],
        budget=60, non_goals=NON_GOALS)


def value_scale() -> Path:
    return gym(
        'Value scale by hatching',
        'Draw a five-step value scale with hatching alone, from near white to near black, each step a distinct band, rendered.',
        needs=[
            STROKES,
            'Five side-by-side rectangles of equal size across the sheet; each filled with hatching only (no fills), the '
            'mean grey of each step darker than the one before it by at least 12% of full scale, the lightest at or '
            'below 15% and the darkest at or above 75%.',
            'Within each step the hatching strokes share a direction within 10 degrees; the two darkest steps use '
            'cross-hatching (a second direction at 60-120 degrees to the first).',
            'The stroke count rises monotonically from the lightest step to the darkest; no step is a single path.',
            ORDER,
        ],
        deliverables=['sketch.svg', 'sketch.png', 'strokes.json', 'plan.md: stroke spacing and width per step'],
        budget=40, non_goals=NON_GOALS)


def perspective_house() -> Path:
    return gym(
        'House in two-point perspective',
        'Draw a simple house (a box with a gable roof, a door, two windows) in two-point perspective on a horizon, as '
        'contour strokes over construction lines, rendered.',
        needs=[
            STROKES,
            'A horizon line across the sheet with two vanishing points on it, both inside the sheet or within half a '
            'sheet width outside it; the house\'s two visible wall faces recede one to each vanishing point.',
            'Every horizontal edge of the house extended meets its vanishing point within 2% of the sheet width; every '
            'vertical edge is vertical within 1 degree; the roof ridge recedes to the same vanishing point as the '
            'wall edge it is parallel to.',
            'Construction lines are drawn first, lighter (grey at or above 70%) and thinner than the contours; the '
            'contours of the house are dark (grey at or below 25%) and heavier.',
            'The door and windows sit on the wall faces and recede with them (their top and bottom edges meet the '
            'face\'s vanishing point within 2% of the sheet width).',
            ORDER,
        ],
        deliverables=['sketch.svg', 'sketch.png', 'strokes.json', 'plan.md: the vanishing points and the house corner in sheet coordinates'],
        budget=50, non_goals=NON_GOALS)


def composition() -> Path:
    return gym(
        'Composition thumbnails',
        'Draw four thumbnail compositions of a landscape with a single building on one sheet, each a different '
        'placement, and mark the one that reads best; the readings are about where the masses sit.',
        needs=[
            STROKES,
            'Four thumbnails in a 2x2 grid, each with a horizon, a building as a dark mass, and at least one hill '
            'contour; in each the horizon sits on a third (28-40% or 60-72% from the top) and the building\'s stroke '
            'mass centre sits on a vertical third (28-40% or 60-72% from the left).',
            'The four differ: no two thumbnails share the same (horizon third, building third) pair.',
            'Each thumbnail has an eye path: a contour or road that runs from the bottom edge toward the building, '
            'its stroke ending within 5% of the sheet from the building mass.',
            'One thumbnail is marked chosen in plan.md with the reason in one sentence; its readings are the ones the '
            'benchmark composition rules use.',
            ORDER,
        ],
        deliverables=['sketch.svg', 'sketch.png', 'strokes.json', 'plan.md: the four placements and the chosen one'],
        budget=40, non_goals=NON_GOALS)


def texture() -> Path:
    return gym(
        'Textures: foliage, grass, shingles',
        'Draw three texture swatches — tree foliage, a grass slope, a shingled roof — each in its own region of the '
        'sheet, with strokes whose lengths, widths and directions read as that material, rendered.',
        needs=[
            STROKES,
            'Three labelled regions of equal size; foliage strokes short and curved (median length 8-40 px, at least '
            '40% of them with a curvature above a stated threshold), grass strokes short and near-vertical (within '
            '25 degrees of vertical, lengths 10-60 px), shingle strokes in staggered horizontal rows (row spacing '
            'regular within 15%, strokes within 10 degrees of horizontal).',
            'Each region has a value gradient: its lower or shadowed half darker than the other half by at least 15% '
            'of full scale, from stroke density, not width alone.',
            LINE, ORDER,
        ],
        deliverables=['sketch.svg', 'sketch.png', 'strokes.json', 'plan.md: the stroke recipe per material'],
        budget=40, non_goals=NON_GOALS)


MAKERS = dict(benchmark=benchmark, value_scale=value_scale, perspective_house=perspective_house,
              composition=composition, texture=texture)


def benchmark_attempt(attempt: int) -> Path:
    """The benchmark from its pinned brief, byte for byte; a benchmark project is measured, never remembered."""
    pinned = json.loads(PINNED.read_text())
    folder = init_module.new_gym('Peaceful home in the countryside, attempt '+str(attempt))
    init_module.init(folder, title=pinned['title'], mission=pinned['mission'], terra=str(TERRA), base=BASE)
    fresh = json.loads((folder/'.mizpah'/'brief.json').read_text())
    keep = {k: pinned[k] for k in pinned if k not in ('created_at', 'updated_at', 'history', 'proposals')}
    (folder/'.mizpah'/'brief.json').write_text(json.dumps(dict(fresh, **keep, proposals=[]), indent=1)+'\n')
    mark_benchmark(folder)
    return folder


def mark_benchmark(folder: Path) -> None:
    path = folder/'.mizpah'/'config.json'
    pc = json.loads(path.read_text())
    pc['benchmark'] = True
    path.write_text(json.dumps(pc, indent=1)+'\n')


# ---------------------------------------------------------------- the ruler

def score(project: Path) -> dict:
    """Readings off sketch.svg / sketch.png / strokes.json: the curve's numbers, taken the same way every time."""
    import re
    svg = project/'sketch.svg'
    png = project/'sketch.png'
    if not svg.exists():
        return dict(ok=False, reason='no sketch.svg')
    text = svg.read_text(errors='replace')
    paths = re.findall(r'<path\b[^>]*>', text)
    widths = [float(m) for p in paths for m in re.findall(r'stroke-width="([0-9.]+)"', p)]
    fills = [p for p in paths if re.search(r'fill="(?!none)', p)]
    colours = set(re.findall(r'stroke="(#[0-9a-fA-F]{3,6}|rgb\([^)]*\))"', text))
    def grey(c: str) -> bool:
        if c.startswith('#'):
            h = c[1:]; h = ''.join(ch*2 for ch in h) if len(h) == 3 else h
            return h[0:2] == h[2:4] == h[4:6]
        nums = [int(float(x)) for x in re.findall(r'[0-9.]+', c)]
        return len(set(nums[:3])) == 1
    out = dict(ok=True, paths=len(paths), filled_paths=len(fills), width_classes=len({round(w, 1) for w in widths}),
               colours=len(colours), all_grey=all(grey(c) for c in colours) if colours else True)
    sj = project/'strokes.json'
    if sj.exists():
        try:
            strokes = json.loads(sj.read_text())
            strokes = strokes if isinstance(strokes, list) else strokes.get('strokes') or []
            roles = [str(s.get('role') or '') for s in strokes]
            order_ok = roles == sorted(roles, key=lambda r: {'construction': 0, 'contour': 1, 'hatching': 2}.get(r, 3))
            out.update(strokes=len(strokes), roles={r: roles.count(r) for r in sorted(set(roles))}, order_ok=order_ok)
        except (ValueError, AttributeError):
            out.update(strokes_json='unreadable')
    if png.exists():
        try:
            from PIL import Image
            import numpy as np
            im = np.asarray(Image.open(png).convert('L'), dtype=float)/255.0
            h, w = im.shape
            hist, _ = np.histogram(1-im, bins=10, range=(0, 1))
            bands = int(sum(1 for i, n in enumerate(hist) if 1 <= i <= 8 and n/im.size >= 0.02))
            rows = 1-im.mean(axis=1)
            sky = float(rows[:int(h*0.25)].mean()); ground = float(rows[int(h*0.75):].mean())
            out.update(png_size=[int(w), int(h)], tonal_bands_2pct=bands, mean_darkness=round(float((1-im).mean()), 3),
                       top_quarter_darkness=round(sky, 3), bottom_quarter_darkness=round(ground, 3))
        except Exception as error:  # noqa: BLE001
            out.update(png_error=str(error)[:120])
    return out


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == 'make' and sys.argv[2] == 'benchmark' and PINNED.exists():
        attempt = int(sys.argv[3]) if len(sys.argv) > 3 else 2
        folder = benchmark_attempt(attempt)
        print(json.dumps(dict(project=str(folder), base=BASE, attempt=attempt, pinned=str(PINNED)), indent=1))
    elif len(sys.argv) >= 3 and sys.argv[1] == 'make':
        folder = MAKERS[sys.argv[2]]()
        if sys.argv[2] == 'benchmark':
            mark_benchmark(folder)
        print(json.dumps(dict(project=str(folder), base=BASE), indent=1))
    elif len(sys.argv) >= 3 and sys.argv[1] == 'score':
        print(json.dumps(score(Path(sys.argv[2]).resolve()), indent=1))
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
