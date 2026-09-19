"""Improve an existing, large, tested codebase: survey it into a map, then change it with the suite as the key.

Usage: python -m fixtures.improve <target_dir> [source_dir]   (from engine/mizpah; makes <target_dir>/improve)

The project is a copy of a real package (default: engine/terra, ~21K lines, 400+ tests). The brief has two
declared phases: survey (readings over the tree — size, public surface, what has no direct
test, the most complex untested function) and change (a test for that function and a complexity reduction,
with the suite still green). "Improve" is a reading or it is nothing. The key is the test suite; targets are
relative where they must be (`<=0.75*need:5`). The worker edits the copy under /work; the terra it runs as a
tool is the installed one, so the two never meet.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import textwrap

from .suite import brief, key_path, phase

DEFAULT_SOURCE = Path(__file__).resolve().parents[2]/'terra'

CONFTEST = '''"""Pin `terra` and `cg` to this copy: an editable install elsewhere on the interpreter wins over pythonpath."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for path in (str(ROOT/'src'), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)
for name in [m for m in sys.modules if m.split('.')[0] in ('terra', 'cg')]:
    del sys.modules[name]
'''

NOTE = (
    'The package is under src/terra with its tests under tests/; `python3 -m pytest -q tests` runs the suite from the '
    'project root (pytest is installed). A function\'s branch count is the number of if/for/while/try/with/boolean-op '
    'nodes in its body (ast.walk); a function has a direct test when its name appears in a test file. A reading about '
    '"that function" takes which function from the map (the probe declares the label known as an input), never decides '
    'it again. Survey before '
    'you change: the readings choose the function. Behaviour must not change: the suite is the reference.'
)


def improve(project: Path, source: Path = DEFAULT_SOURCE) -> None:
    for name in ('src', 'tests', 'cg', 'pyproject.toml', 'README.md'):
        path = source/name
        if not path.exists():
            continue
        if path.is_dir():
            shutil.copytree(path, project/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache'))
        else:
            shutil.copy2(path, project/name)
    # The sandbox's interpreter may carry another terra as an editable install, whose import hook beats
    # pytest's pythonpath: luna's first improve run tested that one (7 failures that were not the copy's).
    (project/'conftest.py').write_text(CONFTEST)
    (project/'content').mkdir()
    (project/'content'/'README.md').write_text(textwrap.dedent('''\
        A copy of a real, tested Python package. src/ is the code, tests/ its suite. The brief asks for a survey of
        the code as readings, then one measured improvement with the suite still green. Nothing here is planted.
        '''))
    needs = [
        'Know the number of lines of Python under src/',                                                       # 1
        'Know the number of public functions (top-level def, no leading underscore) under src/',              # 2
        'Know the number of public functions whose name appears in no file under tests/',                     # 3
        'Know which public function with no direct test has the highest branch count',                        # 4  (label)
        'Know the branch count of that function before any change',                                            # 5
        'Know whether the test suite passes (python3 -m pytest -q tests exits 0) before any change',            # 6
        'Know the number of tests the suite collects before any change',                                       # 7
        'Know the branch count of that function after the change, counted together with every function under src/ '
        'that it calls and that did not exist before the change (moving the body elsewhere is not a reduction)',    # 8
        'Know whether a test file named for that function exists and every test in it passes',                # 9
        'Know whether the test suite passes after the change',                                                 # 10
        'Know the number of tests the suite collects after the change',                                        # 11
    ]
    deliverables = [
        'tests/test_<function>.py: at least three tests of the function the survey chose (need 4), each exercising a '
        'different branch, passing',
        'the function refactored in place so its branch count is at least a quarter lower than before (need 8 against '
        'need 5) with the suite still green (need 10) and no public signature changed',
        'report/improvement.md: the survey readings (needs 1 to 7), the function chosen and why, what was changed, '
        'and the after readings (needs 8 to 11), every number a reading on the map',
    ]
    brief(project, 'Improve a tested codebase', 'Survey an existing 20-thousand-line package into readings, choose the most '
          'complex untested public function, give it tests and a real complexity reduction, and prove the suite still '
          'passes — without ever holding the whole codebase in view.', needs, deliverables, budget=400,
          notes='Two phases: survey first (needs 1 to 7 are readings over the tree, taken before any file changes), then '
                'the change (needs 8 to 11). '+NOTE,
          non_goals=['No `public signature` change: the chosen function keeps its name, parameters and return type',
                     'No `test edit` to make the suite pass: existing tests are the reference; only the new test file is added',
                     'No `wrapper`: moving the body into a new function and leaving a one-line wrapper is not a reduction'])
    # Two phases: the controller routes the survey to closure before a single change unknown can be minted.
    phase(project, 'survey', 'Survey the package into readings', needs='1-7', points=150)
    phase(project, 'change', 'Change one function with the suite as the reference', needs='8-11', deliverables='1-3', points=200)
    key_path(project).write_text(json.dumps(dict(fixture='improve', key=dict(
        by_need={}, targets={'6': True, '8': '<=0.75*need:5', '9': True, '10': True, '11': '>=need:7'})), indent=1)+'\n')


def main() -> None:
    target = Path(sys.argv[1]).resolve()
    source = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else DEFAULT_SOURCE
    project = target/'improve'
    if project.exists():
        raise SystemExit(str(project)+' exists')
    project.mkdir(parents=True)
    improve(project, source)
    print(project)


if __name__ == '__main__':
    main()
