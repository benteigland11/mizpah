# Timed live loop pilot

Execution authorized by the user's Go on 2026-09-16. This extends the approved
three-test proposal; it does not change the controller's role or cadence.

## Feature
Run the existing worker/controller session with reasoning-only generation budgets,
recoverable context overflow handling, and independent deadlines, then grade a small
matched live-model pilot from immutable task fixtures and saved outputs.

## Candidates
1. Native model request/output policy and handoff construction: backend atom.
2. Explicit bounded projections of archived native conversation fields: universal atom.
3. Context fitting, worker-visible archive storage and blocked status: existing
   multi-widget session composition.
4. Process-group deadlines and concurrent output collection: backend atom.
5. Frozen task fixtures, private graders, model bindings and sequential campaign:
   product-specific evaluation wiring.

## Classify
| Implementation | Kind | Reason |
| --- | --- | --- |
| Native persistent session | Widget | Owns model request and conversation protocol |
| Payload projection | Widget | Owns bounded field excerpts and explicit archive references |
| Focused session | Blueprint | Composes model, storage, controller and sandbox state |
| Async job runner | Widget | Owns process lifecycle, deadline and process-tree cleanup |
| Trial fixtures, graders and campaign | Glue | Frozen scenarios and sequencing for this pilot |

## Search
Searched project installs and registry for context budgets, compaction, native sessions,
projection and process deadlines. Existing backend-persistent-model-session-python and
bp-focused-agent-session-python own the native state and composition. Installed
universal-context-payload-projection-python 1.0.1 provides the bounded excerpt machinery;
extend it for native message fields without changing tool identities. Installed
backend-async-job-runner-python 1.0.0 provides the job API; extend it for true deadlines,
process-group cleanup and concurrent stdout/stderr draining. The generic context bundle
blueprint is broader than this native session and does not replace its durable protocol.

## Improve, Install, Create, Blueprint
- Improve backend-persistent-model-session-python: accept -1 output allowance, explicit
  input headroom independent of output caps, optional projected history for a handoff.
- Improve universal-context-payload-projection-python: project dynamic native text and
  oversized arguments into clearly marked excerpts with full archive references.
- Extend bp-focused-agent-session-python: archive only worker-visible history into its
  workspace when projection is needed; fit the handoff using actual tokenizer counts;
  persist an explicit blocked reason if fixed context cannot fit. Keep controller
  reference, memory and private review transcript out of worker archives.
- Improve backend-async-job-runner-python: terminal timeout outcome, independent process
  deadline, process-group termination and simultaneous draining of both output streams.
- Reuse current controller progress, revision store, event log and sandbox widgets.
- Keep trial specification and grader programs, server binding, sequential campaign and
  notification wiring under tools/focused-harness. No new leaf or blueprint is needed.

## Verification and execution
Use deterministic tests for oversized tool batches, immutable archived originals,
native call/result identity, irreducible capacity failure, unrestricted output requests,
restart behavior and deadline cleanup. Validate changed widgets before updating pins.
Qualify the real sandbox and a real model call, including cancellation on a deadline.
Then run one seed across six paired attempts: controller on/off for drift and trajectory,
and 60k/90k thresholds for continuity with controller enabled. Run sequentially on the
dedicated model endpoint. No trajectory steering or repeated interactive polling.
An independent deadline stops each attempt; a scheduled task evaluates terminal results.
Only expand the pilot when the initial attempts establish the intended coverage.

## Qualification amendments

The continuity arm uses twelve explicitly synthetic incident packets, each about
22,000 tokens, with ordered full reads and separate checkpoint turns. This is a
constructed context-stress task, not evidence of natural long-project performance.
It also repairs the seeded ledger and carries packet facts into reconciliation.
Coverage requires twelve complete reads in order, spacing between reads, two or
more actual handoffs and the turn-21 review. Functional grading is independent.

Live qualification exposed fenced final JSON and a missing initial project document.
The parser remains strict. The controller policy explicitly forbids Markdown fences;
a completed but invalid decision now receives validation feedback within the existing
review budget, with persisted rejected response and call count. The worker remains
paused. Failed readiness attempts are retained outside the six scored attempts.

All correct grader controls must pass; the original broken fixtures must fail.
The fixed seed is 731. Controller-off/on order is reversed between the two ordinary
tasks; continuity runs 90k then 60k. This is one attempt per arm, so it measures local
integration and observed behavior, not statistical efficacy. No prior trial scores
are reused. A previously proven unchanged deadline-cancellation control is reused.

## Approved reliability revision

The subsequent Go approves the post-pilot
[fix proposal](../../artifacts/focused-harness/gemma4-pilot-20260916/FIX-PROPOSAL.md).
Use optional null review-call ceilings and voluntary valid decisions, refresh the
project document and fit context before every controller call, preserve retrieval
of raw review evidence, support revision-checked range edits, and archive every
worker rollover with its last unanswered tool action. Maintain the existing cadence,
loaded model, sampling settings, seed and attempt deadlines. The new public fixture
clarifies the currency field in both comparison arms. The original results remain
unchanged, and their exact 32-file runtime is saved in `source-snapshot.tar.gz` in
the original campaign directory.

Qualify long reviews, conflict recovery through restart, post-tool context growth,
historical retrieval, ordinary and overflow handoffs, and independent timeout
failure semantics. The new six-attempt output is
`artifacts/focused-harness/gemma4-pilot-v2-20260916`; fixture and runtime hashes must
be frozen again after qualification. Keep failed qualification attempts separate
from the scored campaign and preserve every scored attempt.
