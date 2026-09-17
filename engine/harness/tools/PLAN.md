# Agentic controller and full-project continuity

Status: implemented and CPU-qualified on 2026-09-16. The original four-field record is replaced by a living project document and a bounded investigation loop. The controller leaf is registered locally at 2.0.1 and the blueprint at 0.2.0. All 33 tests pass. The real-sandbox launcher qualification completed 23 worker turns, seven handoffs, three controller reviews and three disposable checks; it verified historical retrieval and project retention through reopen. Evidence: artifacts/focused-harness/qualification/20260916T163936.284392Z. Live-model compatibility and long-horizon semantic coherence remain unqualified.

## Feature
The controller maintains the full project lifecycle: intended outcomes, current position, completed work, remaining phases, decisions, findings and evidence. It receives the fixed reference and recent worker activity, inspects additional evidence when needed, edits its own nested todos and notes, then steers the worker. The worker retains control of execution and writes its own compaction handoff.

## Candidates
Controller project-document state and validated edits; native model/tool review continuation; workspace and historical-evidence inspection; disposable sandbox checks; journal and restart persistence; launcher configuration.

## Classify
Extend the existing controller state leaf for document state, editing and review decisions. Extend the existing focused-session blueprint for the multi-widget investigation loop and tool dispatch. Keep paths, model choices, prompts and launch wiring in the launcher.

## Search
Searched installed widgets and configured registry for controller progress, agent sessions, project documents, editable text, history query and tool loops. Reuse the existing native persistent session for controller tool batches and restart serialization. The generic AgentToolLoop does not serialize native conversation/pending calls, which the existing session leaf already handles. Fuzzy text editing and versioned document stores do not replace the existing state/journal contract; project-document edits require exact unique matches and revision checks.

## Improve / Install / Create / Blueprint
- Improve universal-controller-progress-python: a single plain-text project document, revision-checked exact edits, bounded reads, existing cadence and held correction semantics. Remove mandatory design/completed/remaining/uncertainty fields.
- Extend bp-focused-agent-session-python: bounded model/tool review loop with project_read, project_edit, workspace_list, workspace_read, history_read and run_check; persist each boundary and continue after reopen.
- Reuse backend-persistent-model-session-python 1.1.0, infra-sandboxed-shell-execution-python 1.0.1, data-session-event-log-python 1.1.0 and infra-revision-store-python 1.1.0 unchanged. Do not change queued experiment sources.
- Update tools/focused-harness configuration, controller instructions, launcher, examples and qualification. No new leaf or blueprint is needed.

## Behavior
Bootstrap after turn 1, then 1+20n, with the last 10 completed worker exchanges as initial evidence. Historical worker exchanges remain available through a read-only tool. Review/model/tool calls do not advance worker cadence. The controller edits its document incrementally; completed work and rationale remain available. The document can carry free-form notes and nested phases without a mandatory internal schema. The immutable reference remains separate.

Only controller project tools can update the document. Workspace inspection reads the frozen worker snapshot. Each run_check gets a disposable sandbox copy; returned workspace changes are never committed. Controller prompts, reference, project document and inspection transcripts never enter the worker context directly. Held correction/evidence/warrant are supplied once per worker request. The controller ends a review with the existing hold/replace/withdraw decision.

Review model/tool/output budgets are explicit settings. Exact token fitting applies to every model request. Journal checkpoints persist the active review conversation, pending tool batch, document changes and guidance. Uncertain dispatched operations block automatic replay. The saved-session schema changes explicitly; old four-field sessions are not silently reinterpreted.

## Verification
Test full-lifecycle document retention across incremental edits, multiple reviews, worker compaction and process reopen; stale/ambiguous edits; inspection beyond the recent window; paginated large evidence; sandbox mutation discard; private reference/document isolation; review budgets and interrupted checks; native multi-tool replay boundaries; unchanged cadence and no-reference behavior. Qualify the actual CLI with scripted loopback model responses and the real sandbox, retaining evidence and confirming original experiment source hashes. Do not claim live-model compatibility or semantic improvement from deterministic qualification.
