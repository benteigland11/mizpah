"""A long-horizon project request: a small tested weather toolkit over a synthetic station network.

Usage: python -m fixtures.weather <target_dir>   (from engine/mizpah)

Eight stations, daily readings for 2025 split by month, an alerts log, a thresholds file.
Planted quirks: a handful of missing values, one impossible humidity, and one fewer WIND
alert than there are gale readings — so cross-file consistency questions have real answers.
The brief asks for readings (needs) and for things to be built from them (deliverables),
under a points budget that the controller has to route within.
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
import json
import math
from pathlib import Path
import random
import subprocess
import sys

TERRA = Path(__file__).resolve().parents[3]/'.venv'/'bin'/'terra'

STATIONS = [
    ('STN01', 'Harbour', 4), ('STN02', 'Airfield', 38), ('STN03', 'Ridge', 612), ('STN04', 'Orchard', 145),
    ('STN05', 'Quarry', 260), ('STN06', 'Lighthouse', 12), ('STN07', 'Summit', 1180), ('STN08', 'Meadow', 88),
]
THRESHOLDS = dict(heat_c=32.0, frost_c=0.0, gale_kmh=75.0)


def terra(project: Path, *args: str) -> None:
    subprocess.run([str(TERRA), *args], cwd=project, capture_output=True, text=True, check=True)


def build_data(project: Path) -> None:
    rng = random.Random(2025)
    with (project/'stations.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['station_id', 'name', 'elevation_m'])
        writer.writerows(STATIONS)
    (project/'thresholds.json').write_text(json.dumps(THRESHOLDS, indent=2)+'\n')
    (project/'readings').mkdir()
    rows_by_month: dict[str, list[list[str]]] = {}
    gales: list[tuple[str, str, float]] = []
    day = date(2025, 1, 1)
    missing_budget = 6
    humidity_spike_done = False
    while day.year == 2025:
        season = math.cos((day.timetuple().tm_yday-200)/365*2*math.pi)  # peak around late July
        for sid, _, elevation in STATIONS:
            temp = 14+11*season-0.0065*elevation+rng.gauss(0, 3)
            humidity = min(99.0, max(20.0, 70-25*season+rng.gauss(0, 10)))
            wind = max(0.0, rng.gammavariate(2.0, 9.0)+(elevation/60))
            rain = 0.0 if rng.random() < 0.55 else round(rng.expovariate(1/6), 1)
            row = [day.isoformat(), sid, f'{temp:.1f}', f'{humidity:.1f}', f'{wind:.1f}', f'{rain:.1f}']
            if missing_budget and rng.random() < 0.002:
                row[rng.choice([2, 3, 4, 5])] = ''
                missing_budget -= 1
            if not humidity_spike_done and day.month == 8 and sid == 'STN06':
                row[3] = '101.3'
                humidity_spike_done = True
            if wind >= THRESHOLDS['gale_kmh']:
                gales.append((day.isoformat(), sid, wind))
            rows_by_month.setdefault(day.strftime('%Y-%m'), []).append(row)
        day += timedelta(days=1)
    for month, rows in rows_by_month.items():
        with (project/'readings'/(month+'.csv')).open('w', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['date', 'station_id', 'temp_c', 'humidity_pct', 'wind_kmh', 'rain_mm'])
            writer.writerows(rows)
    lines = [f'{d}T06:00Z {sid} WIND {w:.1f}' for d, sid, w in gales[:-1]]  # one fewer than the gale readings
    lines += [f'2025-{m:02d}-15T12:00Z STN0{1+m % 8} MAINT scheduled' for m in range(1, 13)]
    (project/'alerts.log').write_text('\n'.join(sorted(lines))+'\n')


NEEDS = [
    'Know how many stations are listed in stations.csv',
    'Know the total number of readings rows across readings/*.csv',
    'Know whether every station has a reading row for every day of 2025',
    'Know the network-wide mean temperature in degrees C across all readings',
    'Know the network-wide maximum temperature',
    'Know the network-wide minimum temperature',
    'Know the mean temperature at the highest-elevation station (per stations.csv)',
    'Know whether the highest-elevation station is colder on average than the lowest-elevation station',
    'Know how many readings are below the frost_c threshold in thresholds.json',
    'Know how many readings are at or above the heat_c threshold in thresholds.json',
    'Know the total rainfall in mm summed over all readings',
    'Know the rainfall total of the wettest month',
    'Know how many readings have wind at or above the gale_kmh threshold',
    'Know how many WIND alert lines alerts.log contains',
    'Know whether the number of WIND alert lines equals the number of gale readings',
    'Know how many readings rows have at least one missing value',
    'Know whether any humidity reading exceeds 100',
    'Know the network-wide mean wind speed',
]
DELIVERABLES = [
    'weather/cli.py, runnable from the project root as `python3 -m weather <command>`, with commands `stations` '
    '(prints the station count), `summary` (prints mean, minimum and maximum temperature), `rain` (prints total '
    'rainfall and the wettest month with its total) and `alerts` (prints the WIND alert count and the gale readings '
    'count); every number it prints agrees with the map',
    'weather/quality.py giving `python3 -m weather quality`, printing the count of rows with missing values and '
    'whether any humidity reading exceeds 100',
    'tests/test_weather.py, runnable from the project root as `python3 -m unittest`, exiting 0, asserting the CLI '
    'output numbers against the values on the map',
    'README.md documenting each command with its actual output',
    'report/climate.md: a per-station table of mean temperature, total rainfall and mean wind speed for 2025',
]


def main() -> None:
    project = Path(sys.argv[1]).resolve()
    if project.exists():
        raise SystemExit(str(project)+' exists')
    project.mkdir(parents=True)
    build_data(project)
    terra(project, 'init')
    terra(project, 'brief', 'init', '--title', 'Weather network toolkit',
          '--mission', 'Turn the 2025 readings of the eight-station weather network into a small, tested '
                       'command-line toolkit and a climate report whose every number agrees with the map.')
    args = ['brief', 'set', '--status', 'active', '--budget-points', '400',
            '--budget-notes', 'Readings first, then the tools that print them, then the tests and documents.']
    for need in NEEDS:
        args += ['--need', need]
    for deliverable in DELIVERABLES:
        args += ['--deliverable', deliverable]
    terra(project, *args)
    terra(project, 'route', 'init')
    print(json.dumps(dict(project=str(project), needs=len(NEEDS), deliverables=len(DELIVERABLES), budget_points=400)))


if __name__ == '__main__':
    main()
