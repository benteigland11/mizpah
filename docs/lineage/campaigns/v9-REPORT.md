# Gemma pilot v9: terminal audit

Ran 2026-09-17 19:22–20:01 UTC, all six arms to a recorded end, 41 minutes total —
the shortest campaign so far. Single seed, fixtures and graders byte-identical to v4–v8.
Runtime changes from v8d are in `plan.json` and the changes file: concern-gated controller
investigation with per-boundary budgets (bootstrap 0, periodic 2, completion 6) and at
most two document edits per review; guard terms limited to backticked tool names;
worker identical-call detection over a six-call window; "after any delete, the first
write is headings or stubs only".

## Outcomes

| Attempt | Checks | Accepted | Turns | Rejected (max identical) | Controller calls / prompt | Concerns | Wall |
|---|---:|---|---:|---:|---|---:|---:|
| drift-off-60000 | 14/14 | Yes | 25 | 1 (2) | – | – | 2.9 min |
| drift-on-60000 | 14/14 | Yes | 34 | 3 (2) | 16 / 161k | 2 | 5.9 min |
| trajectory-on-60000 | 8/8 | Yes | 33 | – | 6 / – | 0 | – |
| trajectory-off-60000 | 4/8 (rollback ×4) | No | 80 | 10 (3) | – | – | 11.3 min |
| continuity-on-90000 | 15/15, protocol 4/12 | No | 64 | 2 (3) | 18 / 303k | 2 | 14.3 min |
| continuity-on-60000 | model returned an empty response at turn 9; session blocked | No | 8 | 0 | 2 / 13k | 0 | 1.0 min |

Campaign totals (v7 → v8d → v9): accepted 3/6 → 4/4 → 3/6; worker turns 634 → 303 → 244;
worker prompt tokens 19.6M → 5.6M → **4.2M**; controller calls 212 → 113 → **42**;
controller prompt tokens 4.28M → 1.91M → **0.54M**; corrections 7 → 2 → 0; wall
194 → 68 → 41 min. Cross-version numbers are a development log, not a controlled
comparison; the on/off pairs within a campaign are the only like-for-like comparison,
and one seed per arm does not resolve outcome differences.

## The controller now reviews instead of re-doing the work

Per-review behavior with budgets in force:

- Bootstrap reviews: 2 model calls each, one document initialization, no concern, hold.
  v8d spent 11–26 calls and up to 6 disposable checks at the same boundary.
- Periodic reviews: 1–4 calls. The one stated concern in drift-on ("has the worker
  written REPORT.md and verified all tests?") spent exactly one `workspace_list`.
- Completion reviews: the real check. drift-on's used its concern on a `workspace_list`,
  two `workspace_read`s and one `run_check`, then held; trajectory-on's tried a read
  without a concern, was refused, and decided from the traces.

Zero corrections and zero guard rejections. On these arms nothing needed correcting, so a
silent controller is the right result; the cost of that silence fell from 1.38M prompt
tokens (v8d trajectory-on) to about 30k. Whether the budgets suppress a needed
investigation cannot be seen on a campaign where none was needed; the continuity
protocol misses (below) are the case to watch.

## Worker

The delete/write cycle is gone: drift-on went from 137 turns and 62 rejections (v8d) to
34 and 3. Reports were built by skeleton and section edits in all four arms that produced
one. trajectory-off failed the four rollback checks (80 turns, 10 rejections): the same
sqlite3 DDL-autocommit mistake as v7, an engineering error the harness does not address.

continuity-90k completed 15/15 functional in 64 turns and 14 minutes (v7: 221 turns, 75
minutes, deadline) but audited only 4 of 12 checkpoint updates as retaining all earlier
facts. The worker rewrote `CHECKPOINT.md` with `write` after several packets rather than
appending, dropping earlier lines; the controller held at turns 21 and 41 with no concern
stated. This is the one place where a periodic review had something to catch and did
not; it is a reference requirement ("update CHECKPOINT.md with the consequential finding
and completed packet number") and would have been a legitimate correction.

continuity-60k: at turn 9 the model returned 38 completion tokens with empty content and
no tool call (`finish_reason: stop`). The harness treats an empty non-final response as
unusable and blocks the session rather than retrying. That is a policy gap: an empty
response should consume one of the configured generation retries like a cancelled
stream does. Not a controller decision; no completion boundary was reached.

## Carried forward

1. Empty worker responses go through the generation-retry path (harness).
2. The continuity checkpoint requirement is exactly the kind of reference constraint a
   periodic review should verify with one cheap concern; whether the controller states it
   is a policy/prompt question, not a budget one.
3. The harness has been stable for a full campaign at a cost an order of magnitude below
   v7. This is the configuration to freeze for the multi-seed on/off comparison once the
   two items above are in.

Model server: healthy throughout v9 (relaunched before the campaign after its second
crash during v8d).
