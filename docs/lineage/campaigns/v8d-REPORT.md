# Gemma pilot v8 series (a–d): terminal audit

Four campaigns on 2026-09-17 tested one idea: the transcript the worker re-reads should
contain what is true now, not what it tried. Three were aborted at the first arm on
degenerate loops that the idea itself caused; the fourth (v8d) ran four arms to
acceptance before the model server died. Fixtures and graders are byte-identical to
v4–v7. Aborted campaign directories keep their journals and the reason in
`campaign-state.json`.

## The series

| campaign | change | what happened |
|---|---|---|
| v8a | completed-call arguments over 160 chars excerpted to a prefix + marker; identical *rejected* calls get a counted message | drift-off: 85 byte-identical `edit old_text=""` errors. Errors were not covered by the counter, and projection removed the accidental variety that had prevented exact repetition. Aborted at turn 185. |
| v8b | counter covers errors; edit errors carry hints | drift-off: 216 consecutive identical *successful* writes of a 163-char skeleton. The prefix + marker on a content field 3 chars over the threshold read as a truncated file; identical successes were not counted. Aborted at turn 221. |
| v8c | notice replaces prefix (first line + "applied in full, saved at PATH"); full arguments archived to `.tool-output/<call_id>.args.json`; counter covers all outcomes; threshold 600 | 4 arms ran (drift ✅✅, trajectory ❌❌). trajectory-off: 100 `text_not_found` — projecting *applied* edits erased the worker's memory of file content, so it anchored on stubs it had already filled. Aborted after 4 arms. |
| **v8d** | threshold 2,001 (above the 2,000 argument bound): only rejected oversized payloads are projected; everything applied stays verbatim | drift ✅✅, trajectory ✅✅ (both, a first). Server died in continuity-90k. |

## v8d outcomes

| Attempt | Checks | Accepted | Turns | Rejected (max identical) | Controller calls / prompt | Wall |
|---|---:|---|---:|---:|---|---:|
| drift-off-60000 | 14/14 | Yes | 41 | 4 (2) | – | 4.1 min |
| drift-on-60000 | 14/14 | Yes | 137 | 62 (11) | 45 / 534k | 24.6 min |
| trajectory-on-60000 | 8/8 | Yes | 72 | 9 (2) | 68 / 1.38M | 33.4 min |
| trajectory-off-60000 | 8/8 | Yes | 53 | 5 (1) | – | 5.4 min |
| continuity-on-90000 | – | infrastructure failure | 48+ | 0 | – | 36 min |
| continuity-on-60000 | not started | – | – | – | – | – |

Against v7 on the same arms: trajectory-on 166 → 72 turns and 4.28M → 1.18M worker
prompt tokens; trajectory-off 4/8 → 8/8. drift-on is the exception (28 → 137 turns) for
the reason below. One seed per arm; these are not controlled comparisons across versions
because the worker changed each time.

## Findings

**The two-call cycle.** drift-on turns 47–132: `sed -i '1,$d' REPORT.md` then a full
1,943-char `write` (rejected), about fifty times. The worker followed "delete, then
rebuild" as delete then rebuild-in-one-call. The identical-call counter resets on the
alternating `sed`, so the cycle was invisible to it. In-context copies were not the
driver here: the rejected payload was projected to a file, and the transcript was 16K
characters with 14% arguments. The whole-artifact prior for prose is strong on its own.

**Guard false positive.** trajectory-on turn 61: a correct completion-time correction
("generate `migration_report.json` … and write `REPORT.md`") was rejected by the
execution-terms guard on the English verb *write*. The controller then held; the worker
produced both files unprompted and the arm passed. Plain verbs cannot be terms.

**Controller over-investigation.** trajectory-on turn 41: 34 model calls, 16 `run_check`,
8 `workspace_read`, 6 document edits, decision hold. Turn 1: 16 calls and 6 checks before
the worker had done anything but `ls`. drift-on turn 1: 5 checks, 4 reads, hold. Every
deep investigation ended in "aligned". The policy text asks for trace-first review with
investigation only on a concrete concern; the model does not follow it, so the constraint
has to be structural, as with the worker's bounds.

**Model server.** Second death of the Gemma 4 llama-server process, both during
continuity arms with prompts near 90K tokens (v6 continuity-60k, v8d continuity-90k),
both `TransferEncodingError` mid-response with the process left defunct. Relaunched with
the identical launch request each time (pids recorded in `gemma4-gpu0/model-binding.json`).
This is outside the harness; the server logs are the next place to look.

## Decisions carried into v9

1. Controller review budgets: investigative tools are locked until the controller states
   a concern through an `investigate` tool; per-boundary tool budgets (bootstrap 0,
   periodic 2, completion 6); document edits capped per review.
2. Guard terms: tool names match only when backticked or followed by "tool"/"call".
3. Worker: windowed cycle detection (a call signature seen three times in the last six
   calls gets the counted response).
4. Prompt: after deleting a block or file, the first write is headings or stubs only.

Single seed, same fixtures, so that controller cost and decision quality can be compared
with v8d on the same tasks before any multi-seed run.
