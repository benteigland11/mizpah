"""Check a weather-fixture project's knowns against the planted answers.

Usage: python -m fixtures.truth <project_dir>   (from engine/mizpah)

The fixture generated the data, so every need has a computable answer; a known that reached the
project map at confidence med with the wrong value is exactly the failure the gate cannot see.
Prints one line per known it can check and exits 1 if any disagree.
"""
from __future__ import annotations

import csv
from datetime import date
import json
from pathlib import Path
import re
import sys

# known id (as the controller has been naming them) → how to compute it from the data
QUANTITIES = {
    'station_count': lambda d: len(d['stations']),
    'total_reading_rows': lambda d: len(d['rows']),
    'all_stations_have_daily_readings': lambda d: len({(r['date'], r['station_id']) for r in d['rows']}) == 365*len(d['stations']),
    'mean_temp_c': lambda d: _mean(d['temps']),
    'max_temp_c': lambda d: max(d['temps']),
    'min_temp_c': lambda d: min(d['temps']),
    'highest_elev_station_mean_temp_c': lambda d: _mean(_col(d, 'temp_c', station=d['highest'])),
    'highest_elev_station_is_colder_than_lowest': lambda d: _mean(_col(d, 'temp_c', station=d['highest'])) < _mean(_col(d, 'temp_c', station=d['lowest'])),
    'frost_reading_count': lambda d: sum(t < d['thresholds']['frost_c'] for t in d['temps']),
    'heat_reading_count': lambda d: sum(t >= d['thresholds']['heat_c'] for t in d['temps']),
    'total_rainfall_mm': lambda d: sum(_col(d, 'rain_mm')),
    'wettest_month_rainfall_mm': lambda d: max(d['rain_by_month'].values()),
    'gale_reading_count': lambda d: sum(w >= d['thresholds']['gale_kmh'] for w in _col(d, 'wind_kmh')),
    'wind_alert_count': lambda d: d['wind_alerts'],
    'wind_alert_matches_gale': lambda d: d['wind_alerts'] == sum(w >= d['thresholds']['gale_kmh'] for w in _col(d, 'wind_kmh')),
    'missing_value_row_count': lambda d: sum(any(v == '' for v in r.values()) for r in d['rows']),
    'any_humidity_exceeds_100': lambda d: any(h > 100 for h in _col(d, 'humidity_pct')),
    'mean_wind_speed': lambda d: _mean(_col(d, 'wind_kmh')),
}
TOLERANCE = 0.05  # numbers the worker rounds to one decimal
# The same answers by brief need number, so a controller that names its unknowns differently is still checked.
BY_NEED = dict(enumerate([QUANTITIES[k] for k in (
    'station_count', 'total_reading_rows', 'all_stations_have_daily_readings', 'mean_temp_c', 'max_temp_c', 'min_temp_c',
    'highest_elev_station_mean_temp_c', 'highest_elev_station_is_colder_than_lowest', 'frost_reading_count',
    'heat_reading_count', 'total_rainfall_mm', 'wettest_month_rainfall_mm', 'gale_reading_count', 'wind_alert_count',
    'wind_alert_matches_gale', 'missing_value_row_count', 'any_humidity_exceeds_100', 'mean_wind_speed')], start=1))


def cited_need(project: Path, known_id: str) -> int | None:
    """The brief need the known's unknown cites (`cites need:N` in its notes), or None."""
    path = project/'.terra'/'map'/'unknowns'/(known_id+'.json')
    if not path.exists():
        return None
    match = re.search(r'need:(\d+)', json.loads(path.read_text()).get('notes') or '')
    return int(match.group(1)) if match else None


def _mean(values: list[float]) -> float:
    return sum(values)/len(values)


def _col(d: dict, field: str, station: str | None = None) -> list[float]:
    return [float(r[field]) for r in d['rows'] if r[field] != '' and (station is None or r['station_id'] == station)]


def load(project: Path) -> dict:
    stations = list(csv.DictReader((project/'stations.csv').open()))
    rows = [r for f in sorted((project/'readings').glob('*.csv')) for r in csv.DictReader(f.open())]
    by_elev = sorted(stations, key=lambda s: int(s['elevation_m']))
    rain_by_month: dict[str, float] = {}
    for r in rows:
        if r['rain_mm'] != '':
            rain_by_month[r['date'][:7]] = rain_by_month.get(r['date'][:7], 0.0)+float(r['rain_mm'])
    d = dict(stations=stations, rows=rows, thresholds=json.loads((project/'thresholds.json').read_text()),
             highest=by_elev[-1]['station_id'], lowest=by_elev[0]['station_id'], rain_by_month=rain_by_month,
             wind_alerts=sum(' WIND ' in line for line in (project/'alerts.log').read_text().splitlines()))
    d['temps'] = _col(d, 'temp_c')
    return d


def check(project: Path) -> list[dict]:
    """One row per checkable known on the project map: expected, found, agree."""
    d = load(project)
    out = []
    for path in sorted((project/'.terra'/'map'/'knowns').glob('*.json')):
        known = json.loads(path.read_text())
        compute = QUANTITIES.get(known['id']) or BY_NEED.get(cited_need(project, known['id']))
        if compute is None:
            continue
        expected = compute(d)
        stats = known.get('stats') or {}
        found = stats.get('mean') if stats.get('kind') == 'number' else (
            None if stats.get('rate') is None else stats['rate'] >= 0.5)  # a boolean's value is its true-rate
        if isinstance(expected, bool):
            agree = found is not None and bool(found) == expected
        else:
            agree = found is not None and abs(float(found)-expected) <= TOLERANCE
        out.append(dict(id=known['id'], expected=expected, found=found, confidence=known.get('confidence'), agree=agree))
    return out


def main() -> None:
    rows = check(Path(sys.argv[1]).resolve())
    for r in rows:
        print(('ok   ' if r['agree'] else 'WRONG')+' '+r['id']+': expected '+str(r['expected'])+', map has '+str(r['found'])+' ('+str(r['confidence'])+')')
    print(str(sum(r['agree'] for r in rows))+'/'+str(len(rows))+' agree')
    raise SystemExit(0 if all(r['agree'] for r in rows) else 1)


if __name__ == '__main__':
    main()
