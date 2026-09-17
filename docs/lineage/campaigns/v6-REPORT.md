# Gemma pilot v6: terminal audit

Ran 2026-09-17 04:34–07:24 UTC. Five of six arms completed; the sixth
(`continuity-on-60000`) failed at 07:24 when the Gemma 4 llama-server process
(pid 2188386) died mid controller response (`TransferEncodingError`, process
defunct). The campaign runner then could not reach `/slots` during cleanup and
recorded `stopped`. That arm is an infrastructure failure at 41 worker turns and is
not a task outcome. The server was relaunched afterwards with the identical recorded
launch request (new pid 2570438, noted in `gemma4-gpu0/model-binding.json`).

Runtime changes from v5 are in `plan.json`: the progressive-refinement worker prompt
with no stated limits, the `read` tool, and rejection messages without figures.
Reasoning retention, the write/edit tools and the 2000/1500/1200 bounds were unchanged.

## Outcomes

| Attempt | Checks | Accepted | Turns | Rejected / errors | Controller decisions | Wall |
|---|---:|---|---:|---:|---|---:|
| drift-off-60000 | 14/14 | Yes | 28 | 1 / 2 | – | 2.8 min |
| drift-on-60000 | 14/14 | Yes | 49 | 9 / 7 | hold ×4 | 9.9 min |
| trajectory-on-60000 | 7/8 (delivery) | No | 125 | 33 / 19 | hold ×8, **replace at 59** | 34.6 min |
| trajectory-off-60000 | 7/8 (delivery) | No | 197 | 8 / 61 | – | 29.2 min |
| continuity-on-90000 | 14/15 | No | 141 | 54 / 4 | hold ×6, **replace at 85, 101, 130** | 75.0 min (deadline) |
| continuity-on-60000 | infrastructure failure | – | 41 | 6 / – | hold ×2 | 17.9 min |

Campaign totals over the five recorded arms, against v4 and v5:

| metric | v4 | v5 | v6 |
|---|---:|---:|---:|
| accepted | 3 / 6 | 3 / 6 | 2 / 5 |
| all functional checks | 5 | 5 | 2 |
| worker turns | 143 | 298 | 540 |
| rejected calls | 0 | 40 | 105 |
| tool errors | 0 | 47 | 93 |
| controller corrections | 0 | 0 | 4 |
| worker prompt tokens | 2.9M | 7.5M | 14.2M |
| controller prompt tokens | 0.7M | 1.7M | 3.8M |
| wall | 45 min | 85 min | 151 min |

## What the refinement method did

**Code got better.** drift-off is the model case: t5–t6 skeletons (`test_ledger.py`
821 chars, `ledger.py` 153 chars), then six edits filling one test each and six
edits filling one concern each of the implementation, one anchor failure in total.
The resulting `ledger.py` is the cleanest of the three campaigns (single pass, ordered
validation, duplicate identity as a `(customer, Decimal, currency)` tuple) with 11
one-behavior tests. v5's `ledger.py` in both drift arms carried a latent bug (whole-row
dict comparison for duplicates) and v5 drift-on had 4 tests; v6 drift-on has 9 with the
conflicting-duplicate case covered. trajectory-off's migration is fully correct,
including the four rollback checks v5 failed.

**Prose did not.** Every v6 failure is a report. trajectory-off never produced
`REPORT.md` (92 edits, 61 errors, all attempts over the bound). trajectory-on's report
contradicted `migration_report.json`; after the controller's correction the worker spent
turns 60–125 unable to fit a rewrite. continuity-90k spent turns 92–141 on the report and
hit the deadline. Reports have no natural seams, so the model shrinks them or loops;
the skeleton-then-fill method was written for code.

**Two thrash mechanisms** account for most wasted turns: replacing a large block with a
larger one in a single `edit` (drift-on t25–t32: eight consecutive rejections on the same
605-char anchor), and byte-identical retries of an already rejected call (four in
drift-on, four in continuity-90k). The 1,200-character edit bound also sat just under
the model's natural chunk for a function body (921–1,025 chars tripped it repeatedly).

## The controller acted for the first time

Four `replace` corrections across three campaigns of zero:

- trajectory-on turn 59 (completion): `REPORT.md` claimed 3 migrated rows; the JSON
  recorded 0. Evidence from `run_check`. The correction was accurate but steered the
  prose toward the JSON, and the JSON was itself wrong (produced by a second, idempotent
  run). Both loops converged on consistency with the wrong artifact. The delivered
  `demo.db` was correct.
- continuity-90k turn 85 (completion): report omitted the twelve packet findings the
  reference requires. Correct.
- continuity-90k turn 101 (periodic): "break the creation of REPORT.md into multiple
  smaller bash commands," after turns 92–101 retried a 3,652-char write. Correct diagnosis
  and the right prescription; the worker did not follow it.
- continuity-90k turn 130 (completion): further report requirements.

Cost remains the controller's problem: bootstrap reviews of 16, 16 and **45** model calls
before the worker had done more than `ls`, and 104 calls / 2.85M prompt tokens on the
continuity arm. This is variance in how deep the controller chooses to investigate, not
a change in policy; it is left as a proposal (see below).

## Decisions carried into v7

Approved by the user before the campaign ended: teach delete-then-rebuild (`sed -i
'a,bd'` the block, then refine it like new code; never replace a block in one call),
anchor on one or two lines, give prose the same skeleton treatment (headings first, one
section per edit); return a firmer message on a byte-identical retry of a rejected
call; name which side of an edit is too large; raise the edit bound to 1,500. No new
tools.

## Proposals not shipped overnight

- **Controller bootstrap depth.** Cap or discourage investigation at turn 1 when the
  worker has only oriented; the policy already says so, the model does not follow it.
- **Controller observation ledger.** Still the largest input-side cost (see the earlier
  audit); unchanged since v4.
- **Completion review grounding.** The trajectory-on correction shows the controller
  needs to compare claimed results against the *reference's* required measurement, not
  only against the worker's other artifacts.

## Evidence

Per-attempt `result.json`, session journals and final workspaces under `attempts/`;
frozen `plan.json`, `fixture-manifest.json`, `source-manifest.json`,
`source-snapshot.tar.gz`; qualification under `qualification/`. Fixtures and graders
are byte-identical to v4 and v5. One seed per arm; outcome differences between
campaigns remain within model variability (the trajectory pair has now gone
pass/fail, fail/pass, pass/fail, fail/fail across v3–v6).
