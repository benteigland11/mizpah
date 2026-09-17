# Worker/controller loop audit

Audited 2026-09-16 against the current focused harness and sample configuration.

The feedback path is implemented and functionally exercised. It is not yet qualified
as an efficient, unattended live-model loop at 60,000–90,000 tokens. The most concrete
remaining defect is insufficient protection of the space needed for worker compaction
after a large tool batch. This audit makes no implementation or configuration changes.

## The implemented cycle

1. The launcher creates the worker from its supplied system prompt and assignment.
   The reference and controller project document remain outside worker context.
   The controller does not write the initial assignment; its first review follows
   the first completed worker turn.
2. The worker responds and executes its requested shell calls. A response and all
   results of its tool batch together count as one completed turn. Files persist;
   command processes do not.
3. With a reference supplied, the controller reviews after turns 1, 21, 41, etc.
   It gets the fixed reference, held guidance, the first page of its project document,
   the latest 10 completed exchanges, and the incoming worker context. A worker final
   answer adds a completion review regardless of the periodic schedule.
4. Each review starts a fresh, bounded model conversation. It can decide from supplied
   evidence, or optionally read files, retrieve older turns, and run checks in a
   disposable workspace copy. It edits its own trajectory document through tools.
   The document persists independently of worker windows and controller conversations.
5. A final controller decision holds, replaces, or withdraws its guidance. The harness
   appends exactly one current guidance message to each subsequent worker request.
   Old guidance messages do not accumulate in the native worker conversation. Edits to
   controller memory alone do not prompt the worker.
6. Before a worker model call, the harness counts the rendered request through the
   configured template/tokenization endpoints. At 60,000 tokens it switches to a
   worker-authored handoff, with a 2,048-token output allowance. The fresh conversation
   contains the unchanged system and assignment, that handoff, and up to three recent
   tool results with a combined 12,000-character content limit. The workspace persists
   and current controller guidance is attached again. The 90,000 figure is capacity,
   not a target amount of old history to retain.
7. At completion, replacement guidance resumes work. Holding or withdrawing guidance
   accepts the proposed final answer. This closes the execution cycle; the correctness
   of acceptance remains a model judgment.

Controller reviews and handoffs do not advance worker-turn cadence. The sample review
budget is eight model calls and sixteen tool calls. Inspection is optional; an empty
project document does require initialization before a controller can finish its review.

## Evidence

- Re-ran the blueprint and controller-state suites: **16 + 17 tests passed**.
- The existing real-sandbox, scripted-model qualification completed 23 worker turns,
  22 worker shell commands, three controller checks, and seven handoffs. Reviews occurred
  at turns 1, 21, and 23. It exercised process reopen, retained controller memory,
  private-reference isolation, old-history retrieval, and discarded check mutations.
  All ten Python source/test/example hashes recorded in that qualification still match.
- That qualification explicitly used synthetic counts and a 500-token rollover trigger
  in a 2,000-token worker capacity. It did not exercise a real model at 60,000–90,000.
  Subsequent controller-policy wording clarifications have not received a live-model
  behavioral evaluation.
- A new deterministic capacity probe reproduced a handoff stall using the actual
  60,000/90,000 sample policy. Its tokenizer assigns one token per serialized character;
  its model and shell are fixtures, so these are **synthetic counts**, not a claim about
  any production tokenizer. The controller is disabled to isolate the worker boundary.

Qualification: [summary](../../artifacts/focused-harness/qualification/20260916T163936.284392Z/summary.json).
Boundary probe: [results](../../artifacts/focused-harness/audit/20260916/compaction-boundary.json),
[reproduction](../../artifacts/focused-harness/audit/20260916/compaction_boundary_probe.py).

## Findings and closing work

### 1. Protect the handoff budget across the entire tool batch

The harness counts before the worker call and waits until every tool result has been
appended before counting again. It reserves the model's output but does not bound the
total token growth from the resulting tool batch.

In the probe, the fifth worker call began at 54,364 synthetic tokens, below the 60,000
trigger, with its response inside the 16,384 output allowance. Four legal tool results,
each within the configured visible stdout/stderr limits, brought the next request to
100,666 tokens. The handoff request was 100,820 before its 2,048 output allowance.
The model was correctly not called, but retrying repeated the same failure. Status
still reported `ready` in phase `handoff`.

Closing change: bound the aggregate context growth of tool batches and preserve space
for the handoff prompt and output. Keep complete evidence available in durable records
or retrievable artifacts while bounding what is inserted into active context. Expose a
capacity stall as blocked, with a defined recovery path. Merely reducing the threshold
does not establish a bound while batch growth remains unbounded.

Relevant code: blueprint `_worker`, `_tool`, `_handoff`, and `status`; persistent-session
`handoff_payload` and `rollover`.

### 2. Make the intended initialization and observation coverage explicit

The present controller behaves like a person checking work and prompting the next
steps, beginning after turn 1. If the intended role also owns the initial delegation,
add a controller initialization phase before the first worker call. That is a proposed
extension, not current behavior or an automatic reason to change the accepted cadence.

The 20-turn cadence and 10-turn recent window deliberately sample the trajectory.
At review 21, only turns 12–21 are supplied; turns 2–11 require history retrieval.
Older evidence is available, but the controller must choose to retrieve it. Preserve
this distinction when assessing whether traces supplied enough evidence for a review.

### 3. Qualify compaction efficiency and continuity on the intended live endpoint

The mechanism uses one extra worker call for a concise handoff and retains a bounded
tail. Its efficiency and semantic continuity have not been measured. Use a bounded
representative task long enough to cross at least two real compaction boundaries and
one periodic review. Record actual prompt/completion tokens, time in handoffs and
controller reviews, request latency before and after reset, and retained workspace and
task facts. Include a milestone completed before reset to detect repeated work, and a
specific unmet requirement to verify completion can resume the worker. Evaluate the
current optional-inspection and trajectory-memory wording in that run.

The reviewed harness configuration is still an example with placeholder model binding;
the saved runs under `artifacts/focused-harness` are qualifications. This audit neither
starts a live run nor establishes host-wide process status or modifies older experiments.

### 4. Distinguish safe stopping from unattended recovery

Model-call exhaustion, oversized controller evidence/context, malformed final JSON,
and uncertain dispatched operations can stop the loop. Explicit uncertainty protection
prevents automatic replay, but the launcher supplies no general recovery action.
Saved settings are immutable on resume. Define bounded recovery for known validation
failures and explicit resolution for uncertain outcomes before claiming unattended
long-duration operation. Full checkpoints and retained event history also grow over
time; storage/reopen cost remains unmeasured at long-run scale.

The next implementation priority is item 1. The next evidence gate is item 3. Initial
controller delegation is a separate product choice; the existing after-turn-1 review
already closes the feedback path once worker execution begins.
