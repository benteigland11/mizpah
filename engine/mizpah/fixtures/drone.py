"""A quadrotor from sizing to component selection: two phases, the second consuming the first's map.

Usage: python -m fixtures.drone <target_dir>   (from engine/mizpah; makes <target_dir>/drone)

Phase `sizing` is the proven estimation brief (twelve readings over drone.json, the report and the script).
Phase `selection` opens only when sizing closes: pick the lightest motor and ESC in catalog.json that meet
the sizing's thrust and current readings, add their real masses to the all-up mass, and re-check whether
the pack sized in phase 1 still meets the flight time — the coherence reading a sum of small briefs never
takes. The airframe mass in drone.json is the bare frame; the selection is what adds the motors and ESCs,
so the honest answer to the re-check may be no, and the report must say so. The deliverable is a bill of
materials, not code; the script is an instrument.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import textwrap

from .suite import brief, key_path, phase

SPEC = dict(
    airframe_mass_kg=1.20, payload_mass_kg=0.35, motors=4, motor_max_thrust_kg=0.90,
    hover_power_per_kg_w=140.0, avionics_power_w=6.0, required_flight_time_min=18.0, reserve_fraction=0.20,
    cell=dict(chemistry='Li-ion 21700', nominal_v=3.6, capacity_ah=4.0, mass_kg=0.070, max_discharge_a=30.0),
    pack_series=6, usable_depth_of_discharge=0.85,
)

CATALOG = dict(
    motors=[   # max thrust is with the paired prop on a 6S pack; mass is the motor alone
        dict(id='m2207_1750', mass_kg=0.032, max_thrust_kg=0.85, max_current_a=28.0, cost=19.0),
        dict(id='m2306_1900', mass_kg=0.034, max_thrust_kg=0.95, max_current_a=32.0, cost=21.0),
        dict(id='m2806_1300', mass_kg=0.058, max_thrust_kg=1.40, max_current_a=38.0, cost=29.0),
        dict(id='m3110_900', mass_kg=0.084, max_thrust_kg=1.90, max_current_a=42.0, cost=41.0),
        dict(id='m4108_480', mass_kg=0.118, max_thrust_kg=2.60, max_current_a=45.0, cost=58.0),
    ],
    escs=[     # continuous current rating; mass is one ESC
        dict(id='esc20', mass_kg=0.006, continuous_a=20.0, cost=9.0),
        dict(id='esc35', mass_kg=0.009, continuous_a=35.0, cost=13.0),
        dict(id='esc45', mass_kg=0.012, continuous_a=45.0, cost=17.0),
        dict(id='esc60', mass_kg=0.019, continuous_a=60.0, cost=24.0),
    ],
    cell_cost=4.5,   # per 21700 cell
)


def size(spec: dict) -> dict:
    s = spec; cell = s['cell']
    parallel = 1
    while True:
        pack_mass = s['pack_series']*parallel*cell['mass_kg']
        aum = s['airframe_mass_kg']+s['payload_mass_kg']+pack_mass
        power = aum*s['hover_power_per_kg_w']+s['avionics_power_w']
        pack_v = s['pack_series']*cell['nominal_v']
        usable = pack_v*cell['capacity_ah']*parallel*s['usable_depth_of_discharge']
        minutes = usable/power*60
        if minutes >= s['required_flight_time_min']*(1+s['reserve_fraction']):
            break
        parallel += 1
    return dict(parallel=parallel, pack_mass=pack_mass, aum=aum, power=power, pack_v=pack_v, usable=usable, minutes=minutes)


def drone(project: Path) -> None:
    (project/'drone.json').write_text(json.dumps(SPEC, indent=2)+'\n')
    (project/'catalog.json').write_text(json.dumps(CATALOG, indent=2)+'\n')
    (project/'README.md').write_text(textwrap.dedent("""\
        # Quadrotor: pack sizing, then component selection

        `drone.json` describes a quadrotor airframe and the cell available for its battery pack. Hover power
        is empirically `hover_power_per_kg_w` watts per kilogram of all-up mass (airframe + payload + pack);
        avionics draw `avionics_power_w` on top. The pack is `pack_series` cells in series; parallel count
        is what needs sizing. Only `usable_depth_of_discharge` of capacity may be used, and `reserve_fraction`
        of the required flight time is kept as reserve. `airframe_mass_kg` is the bare frame: motors and
        ESCs are chosen from `catalog.json` afterwards and add their own mass.

        `catalog.json` lists motors (mass, max thrust with the paired prop, max current, cost), ESCs (mass,
        continuous current, cost) and the cost per cell. A selection is four motors, four ESCs and the pack.
        """))
    s = SPEC; cell = s['cell']; z = size(s)
    aum1 = s['airframe_mass_kg']+s['payload_mass_kg']+s['pack_series']*cell['mass_kg']
    needs = [
        'Know the pack nominal voltage in volts for the series count in drone.json',                                      # 1
        'Know the all-up mass in kg with a one-parallel pack (airframe + payload + 6 cells)',                              # 2
        'Know the hover power draw in watts at that all-up mass, including avionics',                                     # 3
        'Know the required flight time in minutes including the reserve fraction',                                        # 4
        'Know the minimum parallel cell count that meets the required flight time with reserve, accounting for the pack mass it adds',  # 5
        'Know the pack mass in kg at that parallel count',                                                                # 6
        'Know the all-up mass in kg at that parallel count',                                                              # 7
        'Know the usable pack energy in watt-hours at that parallel count',                                               # 8
        'Know the hover flight time in minutes at that parallel count',                                                   # 9
        'Know whether the four motors at maximum thrust lift the all-up mass with at least 1.5 thrust-to-weight ratio',    # 10
        'Know the pack continuous discharge capability in amps at that parallel count',                                   # 11
        'Know whether the hover current draw at pack voltage is within the pack discharge capability',                    # 12
        'Know the hover power draw in watts at the sized all-up mass (need 7), including avionics',                       # 13
        # selection
        'Know the hover current per motor in amps: the hover power at the sized mass (need 13) over pack voltage, over four',  # 14
        'Know which motor id in catalog.json is the lightest whose max thrust times four is at least 1.5 times the sized all-up mass (need 7)',  # 15
        'Know which ESC id in catalog.json is the lightest whose continuous current is at least twice the hover current per motor (need 14)',  # 16
        'Know the selection all-up mass in kg: the sized all-up mass (need 7) plus four of the chosen motor and four of the chosen ESC',  # 17
        'Know the hover flight time in minutes at the selection all-up mass with the pack sized in need 5',                # 18
        'Know whether that flight time still meets the required flight time with reserve (need 4)',                       # 19
        'Know the selection cost: four motors, four ESCs and every cell of the pack at the catalog prices',                # 20
    ]
    deliverables = [
        'report/pack.md stating the chosen series and parallel counts, pack mass, all-up mass, usable energy, '
        'hover power, flight time with reserve and thrust-to-weight ratio, each number agreeing with the map',
        'sizing/pack.py, runnable as `python3 sizing/pack.py drone.json`, printing the same numbers as report/pack.md',
        'bom.csv with one row per part (part, id, quantity, unit mass kg, unit cost) for the motors, ESCs and cells, '
        'and a totals row whose mass and cost agree with the map',
        'report/selection.md: which motor and ESC and why (the readings that chose them), the selection all-up mass, '
        'the re-checked flight time, and a plain statement of whether the pack sized in phase 1 still meets the '
        'requirement — if it does not, say so; do not re-size',
    ]
    brief(project, 'Quadrotor sizing and selection', 'Size the battery pack for the quadrotor in drone.json, then choose '
          'its motors and ESCs from catalog.json and re-check the sizing with the real component masses, so the bill '
          'of materials is traceable to readings.', needs, deliverables, budget=500,
          notes='Numbers by two independent methods where possible; the report and the script print the same figures; '
                'the selection phase consumes the sizing phase\'s knowns by id.')
    phase(project, 'sizing', 'Size the pack', needs='1-13', deliverables='1-2', points=200)
    phase(project, 'selection', 'Choose motors and ESCs, re-check the sizing', needs='14-20', deliverables='3-4', points=200)
    # key
    per_motor = z['power']/z['pack_v']/4
    motor = min((m for m in CATALOG['motors'] if m['max_thrust_kg']*4 >= 1.5*z['aum']), key=lambda m: m['mass_kg'])
    esc = min((e for e in CATALOG['escs'] if e['continuous_a'] >= 2*per_motor), key=lambda e: e['mass_kg'])
    sel_aum = z['aum']+4*motor['mass_kg']+4*esc['mass_kg']
    sel_power = sel_aum*s['hover_power_per_kg_w']+s['avionics_power_w']
    sel_minutes = z['usable']/sel_power*60
    cells = s['pack_series']*z['parallel']
    cost = 4*motor['cost']+4*esc['cost']+cells*CATALOG['cell_cost']
    key_path(project).write_text(json.dumps(dict(fixture='drone', key=dict(by_need={
        1: z['pack_v'], 2: aum1, 3: aum1*s['hover_power_per_kg_w']+s['avionics_power_w'],
        4: s['required_flight_time_min']*(1+s['reserve_fraction']), 5: z['parallel'], 6: z['pack_mass'], 7: z['aum'],
        8: z['usable'], 9: z['minutes'], 10: s['motors']*s['motor_max_thrust_kg'] >= 1.5*z['aum'],
        11: z['parallel']*cell['max_discharge_a'], 12: z['power']/z['pack_v'] <= z['parallel']*cell['max_discharge_a'],
        13: z['power'], 14: per_motor, 15: motor['id'], 16: esc['id'], 17: sel_aum, 18: sel_minutes,
        19: sel_minutes >= s['required_flight_time_min']*(1+s['reserve_fraction']), 20: cost,
    }, tolerance=0.02, relative=True)), indent=1)+'\n')


def main() -> None:
    target = Path(sys.argv[1]).resolve()
    project = target/'drone'
    if project.exists():
        raise SystemExit(str(project)+' exists')
    project.mkdir(parents=True)
    drone(project)
    print(project)


if __name__ == '__main__':
    main()
