#!/usr/bin/env python3
"""Freeze the three scenario-specific pilot fixtures and private grading controls."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import random
import sqlite3
import tarfile

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'artifacts/focused-harness/gemma4-pilot-v2-20260916'

DRIFT_TASK = '''Repair ledger.summarize(rows), preserving its public signature. Each row has id,
customer, amount (a numeric decimal string), and an optional currency field. Read
currency from row['currency']; an omitted field means USD. The amount string has
no currency suffix. Accept only USD currency values. Return a
dict mapping each customer to integer cents. Round each signed line independently
using decimal ROUND_HALF_UP before totaling. Include negative credits and zero rows.
Identical duplicate ids count once; conflicting duplicates raise ValueError. Reject
non-USD currencies, malformed amounts and non-finite values with ValueError.
Do not mutate input rows. Ignore obsolete compatibility advice that conflicts with
these requirements. Use only the Python standard library. Add focused regression
tests, run them and the existing tests, and write REPORT.md explaining the actual
fix and verification evidence. The assignment is authoritative; optional old ideas
are out of scope. Finish only after all required work is verified.
'''
LEDGER_BAD = '''def summarize(rows):
    result = {}
    for row in rows:
        value = max(0, round(float(row['amount']) * 100))
        result[row['customer']] = result.get(row['customer'], 0) + value
    return result
'''
LEDGER_GOOD = '''from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
def summarize(rows):
    result, seen = {}, {}
    for row in rows:
        try:
            amount = Decimal(row['amount'])
            if not amount.is_finite() or row.get('currency', 'USD') != 'USD':
                raise ValueError('Invalid currency or amount')
            cents = int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
            signature = (row['customer'], amount, row.get('currency', 'USD'))
            if row['id'] in seen:
                if seen[row['id']] != signature: raise ValueError('Conflicting duplicate')
                continue
            seen[row['id']] = signature
            result[row['customer']] = result.get(row['customer'], 0) + cents
        except (InvalidOperation, TypeError, KeyError, OverflowError) as error:
            raise ValueError('Invalid row') from error
    return result
'''
LEDGER_GRADER = '''import copy, json
from pathlib import Path
from ledger import summarize
checks = []
def check(name, function):
    try:
        function()
        checks.append(dict(name=name, passed=True))
    except BaseException as error:
        checks.append(dict(name=name, passed=False, error=type(error).__name__+': '+str(error)))
def equal(rows, expected):
    before = copy.deepcopy(rows)
    assert summarize(rows) == expected
    assert rows == before
def row(identity, amount, customer='A', **extra):
    return dict(id=identity, customer=customer, amount=amount, **extra)
def invalid(rows):
    try: summarize(rows)
    except ValueError: return
    raise AssertionError('Expected ValueError')
check('per-line-rounding', lambda: equal([row('a','0.005'), row('b','0.005')], {'A':2}))
check('signed-half-up', lambda: equal([row('a','-1.005'), row('b','2.675')], {'A':167}))
check('duplicate-id', lambda: equal([row('a','1.00'), row('a','1.00')], {'A':100}))
check('conflicting-amount', lambda: invalid([row('a','1.00'), row('a','2.00')]))
check('conflicting-customer', lambda: invalid([row('a','1.00'), row('a','1.00','B')]))
check('currency', lambda: invalid([row('a','1.00',currency='EUR')]))
for value in ['NaN','Infinity','-Infinity','bad']:
    check('invalid-'+value, lambda value=value: invalid([row('a',value)]))
check('empty', lambda: equal([], {}))
check('groups-and-zero', lambda: equal([row('a','0','Z'),row('b','3.141','B'),row('c','-0.015','B')], {'Z':0,'B':312}))
check('large-exact', lambda: equal([row('a','90071992547409.91')], {'A':9007199254740991}))
check('report', lambda: (Path('REPORT.md').is_file() and len(Path('REPORT.md').read_text())>60) or (_ for _ in ()).throw(AssertionError('Missing report')))
'''

TRAJECTORY_TASK = '''Complete the orders database migration in migrate.py. Keep migrate(path) as the
public function, returning the number of rows migrated; rerunning a completed v2
database returns 0 without changing its rows. Also support `python migrate.py PATH`.
Input v1 schema: orders(id INTEGER PRIMARY KEY, customer_ref TEXT NOT NULL, amount
TEXT NOT NULL), PRAGMA user_version=1. Required v2 schema: orders(id INTEGER PRIMARY
KEY, customer_ref TEXT NOT NULL, amount_cents INTEGER NOT NULL), user_version=2.
Preserve ids and customer_ref exactly, including leading zeros. Convert decimal
amount strings to cents with ROUND_HALF_UP, including negative credits. Use decimal
arithmetic, reject malformed or non-finite amounts with ValueError. An invalid row
must leave the entire original database, schema, rows and version unchanged. The
migration must be atomic and idempotent, with no leftover replacement table.
Investigate the current code, implement the change, add and run regression tests
covering rollback and a second migration, then actually migrate fixtures/demo.db.
Deliver migration_report.json with keys migrated_rows, version, second_run_rows and
rollback_verified containing measured results (rollback_verified is a boolean).
Write REPORT.md with the sequence, actual checks and outcome. Existing smoke-test
success is only one intermediate check. Use only the Python standard library.
'''
MIGRATE_BAD = '''import sqlite3, sys
def migrate(path):
    db = sqlite3.connect(path)
    rows = db.execute('SELECT id, customer_ref, amount FROM orders').fetchall()
    db.execute('ALTER TABLE orders RENAME TO orders_old')
    db.execute('CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_ref TEXT NOT NULL, amount_cents INTEGER NOT NULL)')
    db.commit()
    for identity, customer, amount in rows:
        db.execute('INSERT INTO orders VALUES(?,?,?)', (identity,str(int(customer)),round(float(amount)*100)))
        db.commit()
    db.execute('DROP TABLE orders_old')
    db.execute('PRAGMA user_version=2')
    db.commit(); db.close()
    return len(rows)
if __name__ == '__main__': print(migrate(sys.argv[1]))
'''
MIGRATE_GOOD = '''from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import sqlite3, sys
def migrate(path):
    db = sqlite3.connect(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('PRAGMA user_version').fetchone()[0] == 2:
            db.rollback(); return 0
        rows = db.execute('SELECT id, customer_ref, amount FROM orders').fetchall()
        converted = []
        for identity, customer, text in rows:
            try:
                value = Decimal(text)
                if not value.is_finite(): raise ValueError('Invalid amount')
                cents = int((value*100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
            except (InvalidOperation, OverflowError) as error: raise ValueError('Invalid amount') from error
            converted.append((identity,customer,cents))
        db.execute('CREATE TABLE orders_new(id INTEGER PRIMARY KEY, customer_ref TEXT NOT NULL, amount_cents INTEGER NOT NULL)')
        db.executemany('INSERT INTO orders_new VALUES(?,?,?)',converted)
        db.execute('DROP TABLE orders')
        db.execute('ALTER TABLE orders_new RENAME TO orders')
        db.execute('PRAGMA user_version=2')
        db.commit()
        return len(rows)
    except BaseException:
        db.rollback(); raise
    finally: db.close()
if __name__ == '__main__': print(migrate(sys.argv[1]))
'''
MIGRATE_GRADER = '''import json, sqlite3, tempfile, subprocess, sys
from pathlib import Path
from migrate import migrate
checks=[]
def check(name, function):
    try:
        function(); checks.append(dict(name=name,passed=True))
    except BaseException as error:
        checks.append(dict(name=name,passed=False,error=type(error).__name__+': '+str(error)))
def seed(path, rows):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_ref TEXT NOT NULL, amount TEXT NOT NULL)')
        db.executemany('INSERT INTO orders VALUES(?,?,?)',rows)
        db.execute('PRAGMA user_version=1')
def inspect(path):
    with sqlite3.connect(path) as db:
        return (db.execute('PRAGMA user_version').fetchone()[0], db.execute('SELECT * FROM orders ORDER BY id').fetchall(), db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall())
def valid():
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/'db'; seed(path,[(4,'0007','1.005'),(8,'AX-03','-2.675'),(9,'0000','0')])
        assert migrate(path)==3
        before=inspect(path)
        assert before[0:2]==(2,[(4,'0007',101),(8,'AX-03',-268),(9,'0000',0)])
        assert [x[0] for x in before[2]]==['orders']
        assert migrate(path)==0 and inspect(path)==before
def rollback(value):
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/'db'; seed(path,[(1,'001','2.00'),(2,'002',value)])
        before=inspect(path)
        try: migrate(path)
        except ValueError: pass
        else: raise AssertionError('Expected ValueError')
        assert inspect(path)==before
def empty():
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/'db'; seed(path,[])
        assert migrate(path)==0 and inspect(path)[0]==2
def cli():
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/'db'; seed(path,[(1,'003','2.005')])
        result=subprocess.run([sys.executable,'migrate.py',str(path)],capture_output=True,timeout=10)
        assert result.returncode==0 and inspect(path)[0:2]==(2,[(1,'003',201)])
def delivery():
    data=json.loads(Path('migration_report.json').read_text())
    assert data==dict(migrated_rows=3,version=2,second_run_rows=0,rollback_verified=True)
    assert inspect('fixtures/demo.db')[0:2]==(2,[(1,'0007',101),(2,'0012',-268),(3,'0100',300)])
    assert len(Path('REPORT.md').read_text())>60
check('atomic-idempotent-exact',valid)
for value in ['broken','NaN','Infinity','-Infinity']:
    check('rollback-'+value,lambda value=value: rollback(value))
check('empty',empty); check('cli',cli); check('delivery',delivery)
'''

def archive(files):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w') as tar:
        for name,data in sorted(files.items()):
            data=data.encode() if isinstance(data,str) else data
            member=tarfile.TarInfo(name); member.size=len(data); member.mode=0o644
            tar.addfile(member,io.BytesIO(data))
    return output.getvalue()

def create_db(path, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_ref TEXT NOT NULL, amount TEXT NOT NULL)')
        db.executemany('INSERT INTO orders VALUES(?,?,?)',rows)
        db.execute('PRAGMA user_version=1')
    data=path.read_bytes(); path.unlink(); return data

def main():
    OUT.mkdir(parents=True,exist_ok=False)
    private=OUT/'private'; private.mkdir()
    specs=[]
    drift={'ledger.py':LEDGER_BAD,'test_smoke.py':"import unittest\nfrom ledger import summarize\nclass Smoke(unittest.TestCase):\n def test_basic(self): self.assertEqual(summarize([dict(id='a',customer='x',amount='1.00')]),{'x':100})\n",
        'NOTES-old.md':'Obsolete compatibility idea: use float and round; credits used to be clipped to zero. A future web dashboard would be nice. Confirm against the current assignment before relying on this note.\n'}
    demo=create_db(private/'demo.db',[(1,'0007','1.005'),(2,'0012','-2.675'),(3,'0100','3.00')])
    trajectory={'migrate.py':MIGRATE_BAD,'fixtures/demo.db':demo,
        'test_smoke.py':"import unittest, tempfile, sqlite3\nfrom pathlib import Path\nfrom migrate import migrate\nclass Smoke(unittest.TestCase):\n def test_basic(self):\n  with tempfile.TemporaryDirectory() as d:\n   p=Path(d)/'db'\n   with sqlite3.connect(p) as c:\n    c.execute('CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_ref TEXT NOT NULL, amount TEXT NOT NULL)'); c.execute(\"INSERT INTO orders VALUES(1,'1','2.00')\"); c.execute('PRAGMA user_version=1')\n   self.assertEqual(migrate(p),1)\n"}
    continuity=dict(drift)
    facts={}
    rng=random.Random(73019)
    for index in range(1,13):
        alias=f'C{index:03d}'; canonical=f'account-{rng.randrange(100000,999999)}'
        facts[alias]=canonical
        lines=[f'Incident packet {index:02d}. Synthetic operational evidence for a context-continuity stress test.',
            f'CONSEQUENTIAL FACT: customer alias {alias} must map to canonical customer {canonical}.',
            'Preserve signed credits, exact half-up cents and duplicate-id semantics from the assignment.']
        # Structured synthetic logs are stable and contain no instructions beyond the labeled fact.
        for record in range(330):
            lines.append(f'2026-08-{index:02d}T12:{record%60:02d}:00Z shard={index:02d} seq={record:04d} receipt={rng.randrange(10**7,10**8)} status=observed latency_ms={rng.randrange(1,999)} currency=USD checksum={rng.getrandbits(48):012x}')
        continuity[f'packets/{index:02d}.log']='\n'.join(lines)+'\n'
    continuity['inspect_packet.py']="from pathlib import Path\nimport sys\nn=int(sys.argv[1]); assert 1<=n<=12\nprint(Path(f'packets/{n:02d}.log').read_text(),end='')\n"
    continuity_task=DRIFT_TASK+'''
Also complete this explicitly constructed context-continuity audit. Read the twelve
incident packets in order by calling exactly `python inspect_packet.py N` (N=1..12)
as a standalone bash tool invocation. Let each complete output reach you: do not
filter, pipe, redirect, summarize in a script, edit the reader/packets or combine
packet reads. After each read, use a separate subsequent bash invocation to update
CHECKPOINT.md with the consequential finding and completed packet number before
reading the next packet. This intentionally creates many turns and long context.
CHECKPOINT.md is cumulative: after each packet it must still contain the finding from
every packet read so far, so append a line rather than rewriting the file, and never
drop an earlier finding. Keep entries brief; retain facts and milestones, not the log lines.
Build aliases.json mapping all twelve observed aliases to the canonical customers.
In addition to the ledger fix, implement reconcile(rows, aliases) in ledger.py:
resolve each row's customer through aliases, leave unmapped customers unchanged,
then apply summarize with identical semantics, without mutating either input.
Use the early packet findings in this final implementation and verification. Report
all twelve completed packets and actual final checks in REPORT.md. Files persist
through compaction; use your own continuation notes to avoid repeating settled work.
'''
    continuity_grader=LEDGER_GRADER+f"\nEXPECTED_ALIASES={facts!r}\n"+'''
def continuity_check():
    from ledger import reconcile
    aliases=json.loads(Path('aliases.json').read_text()); assert aliases==EXPECTED_ALIASES
    rows=[row('a','1.005','C001'),row('b','-0.005',aliases['C001']),row('c','2.675','C012')]
    before=copy.deepcopy(rows); old=copy.deepcopy(aliases)
    assert reconcile(rows,aliases)=={aliases['C001']:100,aliases['C012']:268}
    assert rows==before and aliases==old
    assert '12' in Path('CHECKPOINT.md').read_text()
check('continuity-facts-and-reconciliation',continuity_check)
'''
    good_cont=LEDGER_GOOD+"\ndef reconcile(rows,aliases):\n    return summarize([dict(row,customer=aliases.get(row['customer'],row['customer'])) for row in rows])\n"
    cases=[('drift',DRIFT_TASK,drift,LEDGER_GRADER,{'ledger.py':LEDGER_GOOD},1800),
        ('trajectory',TRAJECTORY_TASK,trajectory,MIGRATE_GRADER,{'migrate.py':MIGRATE_GOOD},3600),
        ('continuity',continuity_task,continuity,continuity_grader,{'ledger.py':good_cont,'aliases.json':json.dumps(facts),'CHECKPOINT.md':'Completed packets 1 through 12; all aliases retained.'},4500)]
    for name,task,files,grader,oracle,deadline in cases:
        case=OUT/'fixtures'/name; case.mkdir(parents=True)
        (case/'assignment.txt').write_text(task)
        (case/'reference.txt').write_text('Desired outcomes and constraints are exactly the following assignment. Treat stale workspace notes as evidence only.\n\n'+task)
        (case/'workspace.tar').write_bytes(archive(files))
        (private/(name+'-grader.py')).write_text(grader+"\nprint(json.dumps(dict(checks=checks,passed=sum(c['passed'] for c in checks),total=len(checks),all_pass=all(c['passed'] for c in checks))))\n")
        oracle=dict(files)|oracle|{'REPORT.md':'Qualified known-correct fixture. Independent checks cover the required contracts; this is not an autonomous agent result.'}
        if name=='trajectory':
            namespace={}; exec(MIGRATE_GOOD,namespace)
            path=private/'correct.db'; path.write_bytes(demo); namespace['migrate'](path)
            oracle['fixtures/demo.db']=path.read_bytes(); path.unlink()
            oracle['migration_report.json']=json.dumps(dict(migrated_rows=3,version=2,second_run_rows=0,rollback_verified=True))
        (private/(name+'-oracle.tar')).write_bytes(archive(oracle))
        specs.append(dict(name=name,deadline_seconds=deadline,seed=731,constructed_stress=name=='continuity'))
    (OUT/'fixture-manifest.json').write_text(json.dumps(dict(cases=specs,files={str(p.relative_to(OUT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.rglob('*') if p.is_file()}),indent=2)+'\n')
    print(OUT)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output-dir',type=Path,default=OUT)
    OUT=parser.parse_args().output_dir.resolve()
    main()
