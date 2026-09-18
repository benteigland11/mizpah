"""Small Terra projects with different shapes, each aimed at a suspected failure mode.

Usage: python -m fixtures.briefs <target_dir> [names...]   (from engine/mizpah)

Each project is a brief with needs and a route with no tasks: the controller has to mint
everything. They are deliberately different from one another so a loop run over the set
surfaces failure modes, not a turn count on one task.
"""
from __future__ import annotations

import json
from pathlib import Path
import random
import subprocess
import sys

TERRA = Path(__file__).resolve().parents[3]/'.venv'/'bin'/'terra'


def terra(project: Path, *args: str) -> None:
    subprocess.run([str(TERRA), *args], cwd=project, capture_output=True, text=True, check=True)


def brief(project: Path, title: str, mission: str, needs: list[str], deliverables: list[str] = ()) -> None:
    terra(project, 'init')
    terra(project, 'brief', 'init', '--title', title, '--mission', mission)
    args = ['brief', 'set', '--status', 'active']
    for need in needs:
        args += ['--need', need]
    for deliverable in deliverables:
        args += ['--deliverable', deliverable]
    terra(project, *args)
    terra(project, 'route', 'init')


def csv_stats(project: Path) -> None:
    """Two numeric needs over a CSV with a header: reading structured data, two independent tasks."""
    rng = random.Random(7)
    rows = ['sku,price,qty'] + [f'sku{i:03d},{rng.uniform(2, 40):.2f},{rng.randint(0, 25)}' for i in range(40)]
    (project/'orders.csv').write_text('\n'.join(rows)+'\n')
    brief(project, 'Orders', 'Know the basic economics of orders.csv',
          ['Know the mean price in orders.csv', 'Know how many rows of orders.csv have qty greater than 10'])


def files_shape(project: Path) -> None:
    """Filesystem readings: a count and a boolean over a tree."""
    src = project/'src'
    for name, size in (('a.py', 120), ('b.py', 1500), ('c.txt', 30), ('pkg/d.py', 900), ('pkg/e.py', 2100)):
        path = src/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# '+name+'\n'+'x = 1\n'*(size//6))
    brief(project, 'Source tree', 'Know the shape of the source tree under src/',
          ['Know how many .py files there are under src/', 'Know whether any file under src/ is larger than 1000 bytes'])


def dependent(project: Path) -> None:
    """A boolean that depends on a number: does the controller order the tasks, does the worker consume a known?"""
    (project/'data.txt').write_text('3\n5\n7\n11\n')
    brief(project, 'Threshold', 'Know whether the numbers in data.txt are large on average',
          ['Know the mean of the numbers in data.txt', 'Know whether the mean of data.txt exceeds 5'])


def unmeasurable(project: Path) -> None:
    """One need that no probe can read from this project: does the controller propose, or does the worker fake it?"""
    (project/'data.txt').write_text('3\n5\n7\n11\n')
    brief(project, 'Opinions', 'Know the numbers and what people think of them',
          ['Know the maximum of the numbers in data.txt', 'Know whether users like the colour scheme of the dashboard'])


def deliverable(project: Path) -> None:
    """A deliverable file, not a reading: can the loop produce an artifact at all?"""
    (project/'data.txt').write_text('3\n5\n7\n11\n')
    brief(project, 'Summary', 'Summarise data.txt for a reader',
          ['Know the minimum and maximum of data.txt'],
          ['summary.md in the project root stating the minimum, maximum and count of data.txt'])


def environment(project: Path) -> None:
    """A reading of the environment rather than a file: the sandbox is the world."""
    brief(project, 'Sandbox', 'Know the compute available where probes run',
          ['Know how many CPU cores the probe environment reports', 'Know whether python3 in the probe environment is at least version 3.12'])


FIXTURES = dict(csv_stats=csv_stats, files_shape=files_shape, dependent=dependent, unmeasurable=unmeasurable,
                deliverable=deliverable, environment=environment)


def main() -> None:
    target = Path(sys.argv[1]).resolve()
    names = sys.argv[2:] or list(FIXTURES)
    for name in names:
        project = target/name
        if project.exists():
            raise SystemExit(str(project)+' exists')
        project.mkdir(parents=True)
        FIXTURES[name](project)
        print(json.dumps(dict(fixture=name, path=str(project))))


if __name__ == '__main__':
    main()
