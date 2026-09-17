# Gemma pilot v7: terminal audit

Ran 2026-09-17 07:56–11:24 UTC on the relaunched Gemma 4 server (pid 2570438, same
launch request as before). All six arms ran to a recorded end. Runtime changes from v6
are in `plan.json`: the worker prompt now teaches refinement for prose as well as code
(headings first, one section per edit), never replacing a block in one call (read,
`sed -i 'a,bd'`, rebuild by refinement), one-or-two-line anchors, and splitting rather
than shrinking; a byte-identical retry of a rejected call receives a firmer message and
is flagged `repeated`; too-long edit errors name which side is large; the edit bound rose
from 1,200 to 1,500 characters. No new tools. Fixtures and graders are byte-identical to
v4–v6.

## Outcomes

| Attempt | Checks | Accepted | Turns | Rejected (repeated) / errors | Controller decisions | Wall |
|---|---:|---|---:|---:|---|---:|
| drift-off-60000 | 14/14 | Yes | 44 | 5 (0) / 4 | – | 5.0 min |
| drift-on-60000 | 14/14 | Yes | 28 | 2 (0) / 1 | hold ×3 | 8.6 min |
| trajectory-on-60000 | 8/8 | Yes | 166 | 22 (0) / 12 | hold ×10 | 44.6 min |
| trajectory-off-60000 | 4/8 | No | 35 | 6 (0) / 1 | – | 3.7 min |
| continuity-on-90000 | 15/15 | No (deadline) | 221 | 39 (6) / 26 | hold ×6, replace ×6 | 75.0 min |
| continuity-on-60000 | 14/15 (no report) | No (failed, blocked) | 140 | 25 (9) / 14 | hold ×6, replace ×1 | 57.4 min |

Totals against the previous campaigns:

| metric | v4 | v5 | v6 (5 arms) | v7 |
|---|---:|---:|---:|---:|
| accepted | 3 / 6 | 3 / 6 | 2 / 5 | 3 / 6 |
| all functional checks | 5 | 5 | 2 | 4 |
| worker turns | 143 | 298 | 540 | 634 |
| rejected calls (repeated) | 0 | 40 | 105 | 99 (15) |
| tool errors | 0 | 47 | 93 | 58 |
| controller corrections | 0 | 0 | 4 | 7 |
| worker prompt tokens | 2.9M | 7.5M | 14.2M | 19.6M |
| controller prompt tokens | 0.7M | 1.7M | 3.8M | 4.3M |
| wall | 45 min | 85 min | 151 min | 194 min |

## What the prose rule fixed

Every v6 failure was a report. In v7 all four arms that finished normally built
`REPORT.md` as a heading skeleton followed by section edits: drift-on wrote 110 characters
of headings, then three sections with 20–29-character anchors (2,624 characters, six
sections, the longest and most structured report of any campaign); drift-off, trajectory-on
and trajectory-off did the same after one rejected whole-report attempt each. trajectory-on,
which failed in v6 with 50 turns of report rewrites after a controller correction, passed
8/8. The two arms without a report are the two continuity arms that ran out of time.

Delete-then-rebuild was used: 15 `sed -i` block deletions across the campaign (trajectory-on
6, continuity 8), none in earlier campaigns. Anchors shrank to one or two lines in the drift
and trajectory arms. Repeated identical rejections were zero in those four arms; the 15
recorded all occurred in the continuity arms after compactions.

## Where it still breaks: continuity after compaction

Both continuity arms failed on time, not on checks. continuity-90k reached 15/15 at 221 turns
and was cut at the 75-minute deadline in a controller review; continuity-60k failed at 140
turns with no report. The pattern in both is the same: after a handoff the worker abandons
the method and starts rewriting whole files with `write` — 71 writes in the 90k arm, 23 in the
60k arm — and the controller then spends its corrections asking for tests the worker just
deleted (t121, t141, t181, t201 in the 90k arm: "regressing by using `write` to overwrite
`test_ledger.py` with increasingly smaller or incomplete test suites"). The checkpoint
protocol also regressed (packets 1–3 audited clean in 90k; v6 had 9/12).

Two candidate causes, not separated by this run: the worker's own handoff does not carry
the method (the system prompt is retained across compaction, but the worker's recent
history of small edits is not), and the continuity fixture's 40K packet reads consume
most of every window so compaction arrives before a file is finished. The user's design
note already classes this fixture as a compaction stress test rather than a benchmark of
bounded actions.

## Controller

Seven corrections, all at reference level and evidence-grounded; the continuity-90k
sequence (t61 persist findings to the workspace; t73 missing regression tests; t121/t141
stop rewriting the test file smaller; t181/t201 restore the duplicate test) reads as a
controller doing its job against a worker that could not hold a plan across resets. Cost
remains high and variable: bootstrap reviews of 12, 34 and 21 model calls; 52–78 calls on
the continuity arms. The observation-ledger and bootstrap-depth proposals from the v6
report stand.

## trajectory-off

4/8 in 35 turns and 3.7 minutes, with a clean process: tests via bash `cat >>` appends,
report by skeleton, one tool error. The failure is an engineering error, not a harness
effect: DDL inside `with db:` under Python's legacy sqlite3 transaction handling commits
implicitly before `CREATE TABLE`, so a failed row leaves `orders_new` behind and every
rollback check fails. v5 failed the same four checks by a different route (scope cut).

## Evidence

Per-attempt `result.json`, session journals and final workspaces under `attempts/`;
`plan.json`, `fixture-manifest.json`, `source-manifest.json`, `source-snapshot.tar.gz`;
qualification under `qualification/`. One seed per arm; outcome differences remain within
model variability (the trajectory pair is now pass/fail, fail/pass, pass/fail, fail/fail,
pass/fail across v3–v7).
