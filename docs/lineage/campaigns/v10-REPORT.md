# Gemma pilot v10: closing state of the harness

Ran 2026-09-17 20:39–22:13 UTC. Single seed, fixtures and graders byte-identical to
v4–v9. Changes from v9 (`plan.json`, changes file): empty worker responses go through
the generation-retry path; the continuity task text states that `CHECKPOINT.md` is
cumulative. Four arms completed; the model server died during continuity-90k (third
death, all in continuity arms near 90K-token prompts) and the campaign stopped before
continuity-60k started. The user has decided multi-seed runs are not needed: v10 is the
closing state of the harness, and its lessons move into the Terra/Cartograph/Playbook
orchestration.

## Outcomes

| Attempt | Checks | Accepted | Turns | Rejected (max identical) | Controller calls / prompt | Concerns | Wall |
|---|---:|---|---:|---:|---|---:|---:|
| drift-off-60000 | 14/14 | Yes | 30 | 2 (2) | – | – | 2.5 min |
| drift-on-60000 | 14/14 | Yes | 42 | 1 (2) | 21 / 261k | 2 | 8.2 min |
| trajectory-on-60000 | 4/8 (rollback ×4) | No | 65 | 4 (3) | 22 / 246k | 3 | 11.2 min |
| trajectory-off-60000 | 8/8 | Yes | 60 | 9 (2) | – | – | 5.6 min |
| continuity-on-90000 | server died at turn 241 | – | 241 | 2 | 6 corrections, 1 guard rejection | 5 | 66 min |
| continuity-on-60000 | not started | – | – | – | – | – | – |

Against v9 on the same arms: drift-off 25→30 turns, drift-on 34→42, trajectory-on
33→65, trajectory-off 80→60. Worker prompt tokens over the four arms 2.08M (v9: 2.08M
on the same four). The trajectory rollback failure moved from the off arm (v9) to the on
arm (v10): the same sqlite3 DDL-autocommit engineering mistake, seen now in v7, v9 and
v10 on alternating arms. With one seed this is noise, not a controller effect, and it is
the clearest illustration of why single-seed on/off comparisons cannot resolve outcome
differences.

## Controller

Every drift and trajectory review held. Concerns were specific and cheap: drift-on's
completion review noticed the report claimed tests passed without a test run in the
traces and spent its budget confirming it; trajectory-on's turn-61 review noticed
`REPORT.md` had only one section filled after a rejected large write. Neither issued a
correction, and both arms' outcomes were decided by the worker's engineering, not by
alignment.

continuity-90k is the arm the controller was built for, and it worked as designed —
including the failure mode it caused:

- Turns 6–24: the worker did the audit correctly with cumulative `echo … >> CHECKPOINT.md`
  appends (the v9 fixture amendment landed), but skipped packets 04, 08 and 12.
- Turn 54 (completion review): concern "verify all twelve packets were read and
  CHECKPOINT.md updated cumulatively"; evidence "`CHECKPOINT.md` is missing entries for
  packets 04, 08, and 12, which contradicts the worker's claim in `REPORT.md`";
  correction "Complete the context-continuity audit by reading all twelve incident
  packets (1-12) in order …, updating `CHECKPOINT.md` cumulatively after each read".
  A legitimate, reference-grounded catch of exactly the miss v9 let through.
- Turns 57–60: the worker read "all twelve, in order" as start over and wiped
  `CHECKPOINT.md` and `aliases.json` (`echo "" > …`).
- Turn 61: the controller caught the wipe — evidence "the worker wiped `CHECKPOINT.md`
  and `aliases.json` in turns 58-60"; correction "Continue the audit from packet 1 …
  Do not overwrite `CHECKPOINT.md`".
- Turn 81: a correction naming `bash` was rejected by the guard; the accepted follow-up
  said only "Each packet must be read individually, and its findings must be appended".
- Turns 66–240: the worker rebuilt the file, drifting between append, overwrite and
  `sed -i '$d'` repairs, re-reading `CHECKPOINT.md` 30+ times; corrections at 141 and 205
  restated the cumulative requirement. The server died in the turn-241 controller call.

Reading: the controller's error signal was right (the miss was real, the wipe was real,
every correction cited the reference and evidence), but a correction that restates the
whole requirement is read by a small worker as a reset. Corrections need to name the
delta ("packets 04, 08, 12 are missing; the other nine entries are correct and must be
kept"), not the requirement. That is a prompt/policy item for the successor, not a
budget one.

## Development log, v4 → v10

| | v4 | v7 | v8d | v9 | v10 |
|---|---:|---:|---:|---:|---:|
| accepted (completed arms) | 3/6 | 3/6 | 4/4 | 3/6 | 3/4 |
| worker turns | – | 634 | 303 | 244 | 197 (4 arms) |
| worker prompt tokens | – | 19.6M | 5.6M | 4.2M | 2.1M (4 arms) |
| controller calls | – | 212 | 113 | 42 | 43 (+~40 continuity) |
| corrections | – | 7 | 2 | 0 | 6 (all continuity) |

Cross-version numbers are a development log, not a controlled comparison; the worker
and controller changed every version.

## What the harness established

1. Bounded actions plus progressive refinement make a 26B MoE model workable in a 60K
   window at roughly one tenth of the v7 cost; the delete/rebuild loops are gone.
2. Reasoning is not retained; oversized payloads go to files; the wire view stays
   verbatim for everything applied. Prefix cache is preserved by tail-only rewrites.
3. A concern-gated controller reviews at about 1–4 model calls per boundary and only
   investigates against a stated concern. Its corrections are reference-only, enforced
   structurally by the guard.
4. Controller corrections must state the delta from the reference, not the reference.
5. The model server dies under ~90K-token prompts (three of three continuity-90k
   attempts across v6, v8d, v10); the 60K window is the operating point for this model.
6. Reference under-specification (v9 checkpoint) and engineering mistakes (sqlite DDL)
   are not controller problems and the harness does not address them.

Carried into the Terra/Cartograph/Playbook design: the brief as a read-only reference
with propose/accept change control; the map as the persistent working set separate from
the reference; the gate as the mechanical error signal; corrections as deltas.

Model server: relaunched after the campaign (pid 2773244, recorded in
`gemma4-gpu0/model-binding.json` with the death history).
