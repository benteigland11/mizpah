"""Five briefs that span the verifiability spectrum, each with a planted answer key where one can exist.

Usage: python -m fixtures.suite <target_dir> [names...]   (from engine/mizpah)
       python -m fixtures.score <project_dir>              scores any project this suite built

The weather brief tested the easy half of the thesis: every need had a number. These test the other
half — the loop has to *make* a fuzzy ask verifiable: discrete unknowns, readings with runs behind
them, proposals where the brief is under-specified. Each fixture writes `<project>.key.json` beside
the project (never inside it — the worker's sandbox sees the project) so the scorer knows which key applies.

  specs        a folder of markdown specs with planted contradictions; deliverable a consistency report
  estimation   size a drone battery pack from given masses and specs; verified by two methods agreeing
  codebase     a small package; which public functions lack tests, who imports whom; a refactor plan
  runbook      the environment itself is the source; a runbook whose outputs are on the map
  underspec    one need nothing in the project can answer; scored on proposing rather than faking
  sales        the weather brief's style on a different problem: the transfer test for the library
"""
from __future__ import annotations

import json
from pathlib import Path
import random
import subprocess
import sys
import textwrap

TERRA = Path(__file__).resolve().parents[3]/'.venv'/'bin'/'terra'


def terra(project: Path, *args: str) -> None:
    subprocess.run([str(TERRA), *args], cwd=project, capture_output=True, text=True, check=True)


def brief(project: Path, title: str, mission: str, needs: list[str], deliverables: list[str], budget: int,
          notes: str = '', enablers: list[tuple[str, str, str, str]] = (), non_goals: list[str] = ()) -> None:
    """`enablers`: (id, title, path, notes) — instruments the brief needs; a need that names an id waits for it.
    `non_goals`: constraints on method, shown to the worker; a backticked term in one is refused as an artifact."""
    terra(project, 'init')
    terra(project, 'brief', 'init', '--title', title, '--mission', mission)
    args = ['brief', 'set', '--status', 'active', '--budget-points', str(budget)]
    if notes:
        args += ['--budget-notes', notes]
    for need in needs:
        args += ['--need', need]
    for deliverable in deliverables:
        args += ['--deliverable', deliverable]
    for non_goal in non_goals:
        args += ['--non-goal', non_goal]
    for eid, etitle, path, _ in enablers:
        args += ['--enabler', eid+':'+etitle+(':'+path if path else '')]
    terra(project, *args)
    for eid, _, _, enotes in enablers:
        if enotes:
            terra(project, 'brief', 'enabler', eid, 'needed', '--notes', enotes)
    terra(project, 'route', 'init')


def phase(project: Path, phase_id: str, title: str, *, needs: str = '', deliverables: str = '', points: int | None = None) -> None:
    """A phase owning brief entries; with `points`, a sector of the same id caps what its tasks may draw."""
    args = ['brief', 'phase', phase_id, '--title', title]
    if needs:
        args += ['--needs', needs]
    if deliverables:
        args += ['--deliverables', deliverables]
    terra(project, *args)
    if points is not None:
        terra(project, 'route', 'sector-add', phase_id, '--title', title, '--points', str(points))


def stamp(project: Path, name: str, key: dict) -> None:
    # The key lives beside the project, not in it: the worker's sandbox sees the project tree.
    key_path(project).write_text(json.dumps(dict(fixture=name, key=key), indent=1)+'\n')


def key_path(project: Path) -> Path:
    return project.parent/(project.name+'.key.json')


# ---------------------------------------------------------------- specs: claims in prose become booleans

SPECS = {
    'api-gateway.md': """# API gateway

    The gateway terminates TLS and forwards requests to services. Every request carries a request id
    header `X-Request-Id`; the gateway generates one when absent.

    - Upstream timeout: **30 seconds**. Requests exceeding it return 504.
    - Rate limit: **120 requests per minute** per API key, enforced with a sliding window.
    - Maximum request body: **10 MB**.
    - Health endpoint: `GET /healthz` returns 200 with `{"status": "ok"}`.
    """,
    'billing-service.md': """# Billing service

    Billing computes invoices nightly from usage events. It is called only through the gateway.

    - The billing service expects the gateway's upstream timeout to be **45 seconds** for invoice
      generation calls, which can be slow at month end.
    - Invoice amounts are stored in **integer cents**; never floats.
    - Usage events older than **90 days** are purged.
    - Every invoice references the request id that created it.
    """,
    'auth-service.md': """# Auth service

    Auth issues JWTs. Tokens expire after **15 minutes**; refresh tokens after **30 days**.

    - Keys rotate every **24 hours**; the previous key stays valid for one rotation period.
    - Rate limit on `/token`: **60 requests per minute** per client, coordinated with the gateway's
      per-key limit of **120 requests per minute**.
    - Every audit log line includes the request id.
    """,
    'data-retention.md': """# Data retention policy

    - Usage events are retained for **180 days** to support annual reconciliation.
    - Audit logs are retained for **365 days**.
    - Invoices are retained for **7 years**.
    - Personal data export requests are fulfilled within **30 days**.
    """,
    'requirements.md': """# Requirements register

    | id | requirement | owner |
    |----|-------------|-------|
    | R1 | All services propagate `X-Request-Id` | gateway |
    | R2 | Invoices are exact to the cent | billing |
    | R3 | Tokens expire within 15 minutes | auth |
    | R4 | Requests over 10 MB are rejected | gateway |
    | R5 | Export requests fulfilled in 30 days | data |
    | R6 | Every service exposes `GET /healthz` | all |
    """,
}
# Planted: timeout 30 vs 45 (gateway vs billing); usage retention 90 vs 180 days (billing vs
# retention policy); R6 is claimed for all services but only the gateway documents /healthz.


def specs(project: Path) -> None:
    (project/'docs').mkdir()
    for name, body in SPECS.items():
        (project/'docs'/name).write_text(textwrap.dedent(body).lstrip())
    needs = [
        'Know how many markdown documents are under docs/',
        'Know how many requirements the register in docs/requirements.md lists',
        'Know the upstream timeout in seconds that docs/api-gateway.md states',
        'Know the upstream timeout in seconds that docs/billing-service.md expects',
        'Know whether the two documents agree on the upstream timeout',
        'Know the usage-event retention in days that docs/billing-service.md states',
        'Know the usage-event retention in days that docs/data-retention.md states',
        'Know whether the two documents agree on usage-event retention',
        'Know how many documents under docs/ mention the request id header X-Request-Id',
        'Know how many services document a GET /healthz endpoint',
        'Know whether requirement R6 (every service exposes GET /healthz) is documented by every service',
    ]
    deliverables = [
        'report/consistency.md listing every contradiction between documents under docs/ as a row with the two '
        'documents, the two values and the quantity, and a final line stating the number of contradictions found',
        'report/requirements.md stating for each requirement R1..R6 whether the documents under docs/ support it, '
        'with the supporting document named or "unsupported"',
    ]
    brief(project, 'Spec consistency', 'Turn the design documents under docs/ into a checked account of where they '
          'agree, where they contradict each other, and which requirements they actually support.',
          needs, deliverables, budget=250, notes='Readings over the documents first; the reports state only what the map holds.')
    stamp(project, 'specs', dict(by_need={1: 5, 2: 6, 3: 30, 4: 45, 5: False, 6: 90, 7: 180, 8: False, 9: 2, 10: 1,
                                          11: False},
                                 contradictions=2, unsupported=['R6']))


# ---------------------------------------------------------------- estimation: no ground truth, two methods

def estimation(project: Path) -> None:
    spec = dict(
        airframe_mass_kg=1.20, payload_mass_kg=0.35, motors=4, motor_max_thrust_kg=0.90,
        hover_power_per_kg_w=140.0,  # W of electrical power per kg of all-up mass at hover, this airframe
        avionics_power_w=6.0, required_flight_time_min=18.0, reserve_fraction=0.20,
        cell=dict(chemistry='Li-ion 21700', nominal_v=3.6, capacity_ah=4.0, mass_kg=0.070, max_discharge_a=30.0),
        pack_series=6, usable_depth_of_discharge=0.85,
    )
    (project/'drone.json').write_text(json.dumps(spec, indent=2)+'\n')
    (project/'README.md').write_text(textwrap.dedent("""\
        # Drone pack sizing

        `drone.json` describes a quadrotor airframe and the cell available for its battery pack.
        Hover power is empirically `hover_power_per_kg_w` watts per kilogram of all-up mass (airframe +
        payload + pack); avionics draw `avionics_power_w` on top. The pack is `pack_series` cells in
        series; parallel count is what needs sizing. Only `usable_depth_of_discharge` of capacity may be
        used, and `reserve_fraction` of the required flight time is kept as reserve.
        """))
    # reference calc: iterate because pack mass feeds hover power
    s = spec; cell = s['cell']
    parallel = 1
    while True:
        pack_mass = s['pack_series']*parallel*cell['mass_kg']
        aum = s['airframe_mass_kg']+s['payload_mass_kg']+pack_mass
        power = aum*s['hover_power_per_kg_w']+s['avionics_power_w']
        pack_v = s['pack_series']*cell['nominal_v']
        usable_wh = pack_v*cell['capacity_ah']*parallel*s['usable_depth_of_discharge']
        minutes = usable_wh/power*60
        if minutes >= s['required_flight_time_min']*(1+s['reserve_fraction']):
            break
        parallel += 1
    needs = [
        'Know the pack nominal voltage in volts for the series count in drone.json',
        'Know the all-up mass in kg with a one-parallel pack (airframe + payload + 6 cells)',
        'Know the hover power draw in watts at that all-up mass, including avionics',
        'Know the required flight time in minutes including the reserve fraction',
        'Know the minimum parallel cell count that meets the required flight time with reserve, accounting for the pack mass it adds',
        'Know the pack mass in kg at that parallel count',
        'Know the all-up mass in kg at that parallel count',
        'Know the usable pack energy in watt-hours at that parallel count',
        'Know the hover flight time in minutes at that parallel count',
        'Know whether the four motors at maximum thrust lift the all-up mass with at least 1.5 thrust-to-weight ratio',
        'Know the pack continuous discharge capability in amps at that parallel count',
        'Know whether the hover current draw at pack voltage is within the pack discharge capability',
    ]
    deliverables = [
        'report/pack.md stating the chosen series and parallel counts, pack mass, all-up mass, usable energy, '
        'hover power, flight time with reserve and thrust-to-weight ratio, each number agreeing with the map, '
        'and a short paragraph on which assumption the result is most sensitive to',
        'sizing/pack.py, runnable as `python3 sizing/pack.py drone.json`, printing the same numbers as report/pack.md',
    ]
    brief(project, 'Drone pack sizing', 'Size the battery pack for the quadrotor in drone.json so it meets the '
          'required flight time with reserve, and document the sizing so every number is traceable.',
          needs, deliverables, budget=300, notes='Numbers by two independent methods where possible; the report '
          'and the script print the same figures.')
    p = parallel; pack_mass = s['pack_series']*p*cell['mass_kg']; aum = s['airframe_mass_kg']+s['payload_mass_kg']+pack_mass
    power = aum*s['hover_power_per_kg_w']+s['avionics_power_w']; pack_v = s['pack_series']*cell['nominal_v']
    usable = pack_v*cell['capacity_ah']*p*s['usable_depth_of_discharge']
    aum1 = s['airframe_mass_kg']+s['payload_mass_kg']+s['pack_series']*cell['mass_kg']
    stamp(project, 'estimation', dict(by_need={
        1: pack_v, 2: aum1, 3: aum1*s['hover_power_per_kg_w']+s['avionics_power_w'],
        4: s['required_flight_time_min']*(1+s['reserve_fraction']), 5: p, 6: pack_mass, 7: aum, 8: usable,
        9: usable/power*60, 10: s['motors']*s['motor_max_thrust_kg'] >= 1.5*aum, 11: p*cell['max_discharge_a'],
        12: power/pack_v <= p*cell['max_discharge_a']}, tolerance=0.02, relative=True))


# ---------------------------------------------------------------- codebase: readings over code

CODE = {
    'shop/__init__.py': '"""A tiny shop domain."""\n',
    'shop/money.py': textwrap.dedent('''\
        """Money helpers in integer cents."""


        def to_cents(amount: float) -> int:
            """Round a float amount to integer cents."""
            return int(round(amount*100))


        def add(a: int, b: int) -> int:
            return a+b


        def apply_discount(cents: int, percent: float) -> int:
            """Apply a percentage discount, rounding half up."""
            return int(round(cents*(1-percent/100)))


        def _clamp(value: int, low: int, high: int) -> int:
            return max(low, min(high, value))
        '''),
    'shop/cart.py': textwrap.dedent('''\
        """Cart totals."""
        from shop.money import add, apply_discount


        def line_total(unit_cents: int, quantity: int) -> int:
            return unit_cents*quantity


        def cart_total(lines: list[tuple[int, int]], discount_percent: float = 0.0) -> int:
            total = 0
            for unit, qty in lines:
                total = add(total, line_total(unit, qty))
            return apply_discount(total, discount_percent)


        def is_empty(lines: list) -> bool:
            return len(lines) == 0
        '''),
    'shop/tax.py': textwrap.dedent('''\
        """Tax computation."""
        from shop.money import to_cents
        from shop.cart import cart_total


        def tax_for(lines, rate_percent: float) -> int:
            return to_cents(cart_total(lines)*rate_percent/100/100)


        def gross(lines, rate_percent: float) -> int:
            return cart_total(lines)+tax_for(lines, rate_percent)
        '''),
    'shop/report.py': textwrap.dedent('''\
        """Text report of a cart."""
        from shop.cart import cart_total, is_empty
        from shop.tax import gross


        def render(lines, rate_percent: float) -> str:
            if is_empty(lines):
                return "empty cart"
            return f"net {cart_total(lines)} gross {gross(lines, rate_percent)}"
        '''),
    'tests/__init__.py': '',
    'tests/test_money.py': textwrap.dedent('''\
        import unittest
        from shop.money import to_cents, add


        class MoneyTest(unittest.TestCase):
            def test_to_cents(self):
                self.assertEqual(to_cents(1.005), 100)

            def test_add(self):
                self.assertEqual(add(1, 2), 3)
        '''),
    'tests/test_cart.py': textwrap.dedent('''\
        import unittest
        from shop.cart import line_total, cart_total


        class CartTest(unittest.TestCase):
            def test_line_total(self):
                self.assertEqual(line_total(250, 2), 500)

            def test_cart_total(self):
                self.assertEqual(cart_total([(250, 2), (100, 1)]), 600)
        '''),
}
# Public functions: money 3 (to_cents, add, apply_discount; _clamp private), cart 3, tax 2, report 1 = 9.
# Tested: to_cents, add, line_total, cart_total = 4. Untested: apply_discount, is_empty, tax_for, gross, render = 5.
# Imports: cart->money, tax->money, tax->cart, report->cart, report->tax = 5 edges; longest chain report->tax->cart->money = 3.


def codebase(project: Path) -> None:
    for name, body in CODE.items():
        path = project/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    needs = [
        'Know how many Python modules are under shop/ (excluding __init__.py)',
        'Know how many public functions (no leading underscore) shop/ defines in total',
        'Know how many of those public functions are called directly by a test under tests/',
        'Know how many public functions no test under tests/ calls',
        'Know how many import edges exist between modules under shop/ (module A imports from module B)',
        'Know the length of the longest import chain between shop/ modules',
        'Know whether tests/ passes when run as `python3 -m unittest` from the project root',
        'Know how many public functions in shop/ have no docstring',
        'Know whether shop/money.py is imported by every other module under shop/, directly or through another module',
    ]
    deliverables = [
        'report/audit.md with a table of every public function in shop/ (module, name, tested yes/no, docstring yes/no) '
        'and a final line stating the untested count, agreeing with the map',
        'report/refactor.md proposing an order in which to add tests, one function per line, covering exactly the untested '
        'functions the map names, most-depended-on first',
    ]
    brief(project, 'Codebase audit', 'Audit the shop/ package for test coverage and coupling, and turn the '
          'findings into a refactor plan whose every claim is a reading over the code.',
          needs, deliverables, budget=250, notes='Readings by parsing the code, not by guessing from names.')
    stamp(project, 'codebase', dict(by_need={1: 4, 2: 9, 3: 4, 4: 5, 5: 5, 6: 3, 7: True, 8: 7, 9: True},
                                    untested=['apply_discount', 'is_empty', 'tax_for', 'gross', 'render']))


# ---------------------------------------------------------------- runbook: the environment is the source

def runbook(project: Path) -> None:
    (project/'services.json').write_text(json.dumps(dict(services=[
        dict(name='model-server', url='http://127.0.0.1:58080/health', expect='"status"'),
        dict(name='runner', url='http://127.0.0.1:58000/models', expect='['),
        dict(name='nothing-here', url='http://127.0.0.1:59999/', expect='never'),
    ]), indent=2)+'\n')
    needs = [
        'Know how many CPU cores the probe environment reports',
        'Know the total memory in gigabytes the probe environment reports',
        'Know the Python version (major.minor) of python3 in the probe environment',
        'Know whether the terra, playbook and cartograph commands are all on PATH in the probe environment',
        'Know how many of the services in services.json answer an HTTP request with their expected text',
        'Know which service in services.json does not answer',
        'Know the free space in gigabytes on the filesystem holding the project root',
        'Know how many environment variables are set in the probe environment',
        'Know whether the probe environment can reach https://pypi.org',
    ]
    deliverables = [
        'runbook/environment.md documenting the probe environment: cores, memory, Python version, disk free, and '
        'for each service in services.json whether it answered, every value agreeing with the map',
        'runbook/check.sh, runnable as `bash runbook/check.sh`, printing one line per service in services.json '
        'with its name and up/down, and exiting 0',
    ]
    brief(project, 'Environment runbook', 'Describe the environment probes run in and the services it can reach, '
          'as a runbook whose every statement was measured, not assumed.',
          needs, deliverables, budget=200, notes='The world is the source; take readings, do not recall.')
    stamp(project, 'runbook', dict(by_need={6: 'nothing-here'}, live=[1, 2, 3, 4, 5, 7, 8, 9]))


# ---------------------------------------------------------------- underspec: a need nothing here can read

def underspec(project: Path) -> None:
    rng = random.Random(11)
    rows = ['day,visitors,signups'] + [f'2025-06-{d:02d},{rng.randint(200, 900)},{rng.randint(5, 60)}' for d in range(1, 31)]
    (project/'funnel.csv').write_text('\n'.join(rows)+'\n')
    visitors = [int(r.split(',')[1]) for r in rows[1:]]; signups = [int(r.split(',')[2]) for r in rows[1:]]
    needs = [
        'Know the total visitors in funnel.csv',
        'Know the total signups in funnel.csv',
        'Know the signup conversion rate as a fraction (signups over visitors)',
        'Know the day with the highest conversion rate',
        'Know why conversion dropped in the second half of the month',
        'Know whether users found the signup form confusing',
        'Know the conversion rate of our main competitor',
    ]
    deliverables = [
        'report/funnel.md stating total visitors, total signups, the conversion rate and the best day, each agreeing '
        'with the map, and listing the questions the data could not answer',
    ]
    brief(project, 'Funnel review', 'Report on the June signup funnel from funnel.csv, and be explicit about what '
          'the data cannot say.', needs, deliverables, budget=150,
          notes='A question the file cannot answer is proposed back to the brief, never answered.')
    rates = [s/v for s, v in zip(signups, visitors)]
    stamp(project, 'underspec', dict(by_need={1: sum(visitors), 2: sum(signups), 3: sum(signups)/sum(visitors),
                                              4: rows[1+rates.index(max(rates))].split(',')[0]},
                                     unanswerable=[5, 6, 7]))


# ---------------------------------------------------------------- sales: the weather brief's style, a different problem

def sales(project: Path) -> None:
    """Same shape as the weather brief — CSV readings, thresholds, a CLI, a quality command, a report —
    with different columns, quirks and questions, so only method and parts can carry over."""
    import csv as _csv
    from datetime import date, timedelta
    rng = random.Random(4242)
    stores = [('S01', 'Harbour', 'north'), ('S02', 'Airfield', 'north'), ('S03', 'Ridge', 'east'), ('S04', 'Orchard', 'east'),
              ('S05', 'Quarry', 'south'), ('S06', 'Lighthouse', 'south'), ('S07', 'Summit', 'west'), ('S08', 'Meadow', 'west')]
    with (project/'stores.csv').open('w', newline='') as handle:
        writer = _csv.writer(handle); writer.writerow(['store_id', 'name', 'region']); writer.writerows(stores)
    limits = dict(large_order_eur=250.0, refund_rate_alert=0.08, min_daily_orders=3)
    (project/'limits.json').write_text(json.dumps(limits, indent=2)+'\n')
    (project/'orders').mkdir()
    rows_by_week: dict[str, list] = {}
    refunds = []
    order_no = 1000
    day = date(2025, 3, 3)  # a Monday
    dup_done = False; negative_done = False
    while day < date(2025, 5, 26):  # 12 weeks
        week = day.strftime('%G-W%V')
        for sid, _, region in stores:
            n = max(0, int(rng.gauss(6 if region in ('north', 'east') else 4, 2)))
            for _ in range(n):
                order_no += 1
                qty = rng.randint(1, 6)
                unit = round(rng.uniform(4, 80), 2)
                total = round(qty*unit, 2)
                row = [f'O{order_no}', day.isoformat(), sid, str(qty), f'{unit:.2f}', f'{total:.2f}']
                if not negative_done and day.month == 4 and sid == 'S03':
                    row[3] = '-2'; negative_done = True
                rows_by_week.setdefault(week, []).append(row)
                if not dup_done and day.month == 4 and sid == 'S06' and len(rows_by_week[week]) > 3:
                    rows_by_week[week].append(list(row)); dup_done = True   # the same order id twice
                if rng.random() < 0.06:
                    refunds.append((day.isoformat(), f'O{order_no}', sid, total))
        day += timedelta(days=1)
    for week, rows in rows_by_week.items():
        with (project/'orders'/(week+'.csv')).open('w', newline='') as handle:
            writer = _csv.writer(handle); writer.writerow(['order_id', 'date', 'store_id', 'qty', 'unit_price_eur', 'total_eur']); writer.writerows(rows)
    lines = [f'{d} {oid} {sid} REFUND {t:.2f}' for d, oid, sid, t in refunds[:-2]]  # two refunds never logged
    lines.append('2025-04-30 O9999 S05 REFUND 12.50')   # a refund for an order that does not exist
    (project/'refunds.log').write_text('\n'.join(sorted(lines))+'\n')
    all_rows = [r for rows in rows_by_week.values() for r in rows]
    ids = [r[0] for r in all_rows]
    totals = [float(r[5]) for r in all_rows]
    by_store: dict[str, list[float]] = {}
    for r in all_rows: by_store.setdefault(r[2], []).append(float(r[5]))
    by_week = {w: sum(float(r[5]) for r in rows) for w, rows in rows_by_week.items()}
    region_of = {sid: region for sid, _, region in stores}
    north = [float(r[5]) for r in all_rows if region_of[r[2]] == 'north']
    west = [float(r[5]) for r in all_rows if region_of[r[2]] == 'west']
    needs = [
        'Know how many stores stores.csv lists',
        'Know the total number of order rows across orders/*.csv',
        'Know how many distinct order ids the rows carry',
        'Know whether every order id is unique across orders/*.csv',
        'Know the total revenue in EUR summed over total_eur',
        'Know the mean order total in EUR',
        'Know the largest single order total in EUR',
        'Know how many orders are at or above large_order_eur in limits.json',
        'Know the revenue of the best week (the orders/ file with the highest total_eur sum)',
        'Know the store id with the highest revenue',
        'Know whether the north region out-sells the west region in total revenue',
        'Know how many order rows have a qty at or below zero',
        'Know how many refund lines refunds.log contains',
        'Know how many refund lines name an order id that does not appear in orders/*.csv',
        'Know the refund rate (refund lines over order rows) as a fraction',
        'Know whether the refund rate is at or above refund_rate_alert in limits.json',
        'Know how many store-days have fewer than min_daily_orders orders (counting only days a store has any order)',
        'Know the mean qty per order',
    ]
    deliverables = [
        'sales/cli.py, runnable from the project root as `python3 -m sales <command>`, with commands `stores` (prints the store '
        'count), `revenue` (prints total revenue, the mean order total and the largest order), `weeks` (prints the best week '
        'and its revenue) and `refunds` (prints the refund line count and the refund rate); every number it prints agrees with the map',
        'sales/quality.py giving `python3 -m sales quality`, printing the count of rows with qty at or below zero and whether '
        'every order id is unique',
        'tests/test_sales.py, runnable from the project root as `python3 -m unittest`, exiting 0, asserting the CLI output '
        'numbers against the values on the map',
        'README.md documenting each command with its actual output',
        'report/stores.md: a per-store table of order count, total revenue and mean order total for the period',
    ]
    brief(project, 'Sales ledger toolkit', 'Turn twelve weeks of order files from eight stores into a small, tested '
          'command-line toolkit and a per-store report whose every number agrees with the map.',
          needs, deliverables, budget=400, notes='Readings first, then the tools that print them, then the tests and documents.')
    from collections import Counter
    dayc = Counter((r[1], r[2]) for r in all_rows)
    order_ids = set(ids)
    dangling = 1+sum(1 for _, oid, _, _ in refunds[:-2] if oid not in order_ids)
    best_store = max(by_store, key=lambda k: sum(by_store[k]))
    stamp(project, 'sales', dict(by_need={
        1: 8, 2: len(all_rows), 3: len(order_ids), 4: len(order_ids) == len(all_rows), 5: round(sum(totals), 2),
        6: sum(totals)/len(totals), 7: max(totals), 8: sum(t >= limits['large_order_eur'] for t in totals),
        9: max(by_week.values()), 10: best_store, 11: sum(north) > sum(west), 12: sum(int(r[3]) <= 0 for r in all_rows),
        13: len(lines), 14: dangling, 15: len(lines)/len(all_rows), 16: len(lines)/len(all_rows) >= limits['refund_rate_alert'],
        17: sum(1 for v in dayc.values() if v < limits['min_daily_orders']), 18: sum(int(r[3]) for r in all_rows)/len(all_rows)},
        tolerance=0.05))


FIXTURES = dict(specs=specs, estimation=estimation, codebase=codebase, runbook=runbook, underspec=underspec, sales=sales)


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
