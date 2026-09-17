# Focused harness: first implementation slice

Design and Cartograph plan, 2026-09-15. No feature code or new evaluation was launched for this plan.

## Feature

Build one reusable, persistent agent session whose normal cycle is a small purposeful action, a bounded observation, and targeted retrieval of durable evidence. The first implementation slice is the evidence cycle across context compaction. Its behavioral requirements and qualification cases are in [the compaction study design](compaction-study-design.md).

Design the retrieval contract before implementing that cycle. The [query search schema](query-search-schema.md) develops a shared search interface and indexed query catalog with Markdown views: reusable questions, replayable selectors, evidence-backed answers, and explicit freshness after source changes. It narrows the first milestone to finding, saving, refreshing, and recovering useful queries across compaction, then using their answers to execute and verify a task action. The catalog distinguishes cumulative knowledge from questions blocking the current action.

The [persistent working-set design](bounded-context-working-set.md) adds the committed 60–90K context constraint, direct reuse of active findings, incremental invalidation, and a checkpoint that reconstructs the current investigation. Its component plan adds existing budget, continuation-record and dependency-impact leaves to this proposed composition. Context resets should resume useful work without repeating broad discovery.

The existing experiment remains evidence about its frozen harness and reviewer. It is not a prerequisite for this design and does not isolate the newly proposed focused-turn policy. Preserve its results and amendments as diagnostics; qualify the new evidence cycle before attributing performance differences to compaction or feedback control.

## Candidates

1. Durable model session, native tool-call identity, context token counting and rollover: backend, Python, existing atom.
2. Sequential model/tool state machine: universal, Python, existing atom.
3. Isolated command execution and streaming stdout/stderr capture: infra, Python, existing atom to extend.
4. Immutable evidence storage, atomic finalization, quota accounting and stable receipts: infra, Python, new atom after search.
5. Bounded text search, ranges and structured field selection over approved evidence: backend, Python, existing atom to install and extend.
6. Durable journal and revision checkpoints: data and infra, Python, existing atoms.
7. Session assembly linking those capabilities with fixed-reference restoration and usage accounting: reusable composition.
8. Task assignment, model endpoint, experimental thresholds and independent grader invocation: product-specific wiring.

## Classify

| Capability | Kind | Public responsibility |
|---|---|---|
| Session transport and context state | Widget | Preserve exact model/tool messages and rebuild bounded context |
| Tool-loop state | Widget | Track ordered requests, results and explicit terminal state |
| Shell execution | Widget | Execute in isolation and report known command/capture outcomes |
| Evidence storage | Widget | Finalize immutable artifacts and resolve durable receipts |
| Evidence inspection | Widget | Retrieve bounded, attributable slices without arbitrary host access |
| Journal and checkpoints | Widgets | Preserve event identity and recover committed state |
| Focused agent session | Blueprint | Own the reusable action, evidence, continuation and compaction cycle |
| One experiment's assignment and grader | Glue | Supply task-specific inputs and score completed outcomes |

## Search

Project inspection and local-library/registry searches covered persistent sessions, shell capture, agent tool loops and blueprints, artifact/blob storage, and bounded workspace read/search. Registry results were deduplicated against local-library hits.

- Installed `backend-persistent-model-session-python` 1.1.0 provides direct transport, native tool state, exact template token counting, and explicit rollover.
- Installed `universal-agent-tool-loop-python` 1.2.0 provides the sequential tool loop.
- Installed `infra-sandboxed-shell-execution-python` 1.0.1 provides isolation and bounded capture. Its current worker buffers streams, writes logs into the workspace, and stops at the capture limit. This is not the proposed independent streaming evidence store.
- Installed `data-session-event-log-python` 1.1.0 and `infra-revision-store-python` 1.1.0 provide the durable journal and checkpoints.
- Library `backend-llm-workspace-read-tools-python` 1.2.0 provides root-scoped `read_file`, `list_dir`, and content-search `glob`. Source inspection found line limits but whole-file text loading in `read_file`; long single-line content therefore needs a real byte/work bound. Extend it rather than duplicating its path confinement and tool metadata.
- Library `backend-streaming-artifact-extractor-python` 1.0.0 parses marker-delimited artifacts from model text. It is not a durable binary evidence store or shell-output sink and is excluded.
- Searches for content-addressed immutable artifact/blob storage found no suitable storage capability. Canonical JSON hashing and dependency-staleness helpers do not supply it.
- Installed `bp-autonomous-laboratory-trial-python` 0.3.0 already composes many of the foundations, but its constructor, state and dispatch require the mechanics laboratory. Reuse its orchestration lessons and the same leaf APIs; keep the new general session independently usable without laboratory or grading dependencies. This avoids presenting the domain-bound experimental runner as the general harness.

## Improve, Install, Create, Blueprint

**Use as-is:** persistent model session, agent tool loop, session event log, and revision store. Preserve their identities and ordering guarantees. A prototype may reveal an API gap; record that before changing a shared dependency.

**Improve:** sandboxed shell execution. Separate small model-visible receipts from complete capture, stream output under an explicit artifact quota, and expose complete/incomplete capture and command status accurately. Finalized evidence must not be copied into every subsequent mutable-workspace snapshot.

**Install and improve:** `backend-llm-workspace-read-tools-python` 1.2.0. Add byte-bounded reads and searches, explicit incomplete-result metadata and structured JSON field selection with bounded processing. The blueprint restricts these tools to approved worker evidence; root confinement is not a substitute for the existing shell's execution isolation.

**Create leaf:** `agent-evidence-store`, infra, Python; proposed id `infra-agent-evidence-store-python`. Public operations should open a bounded capture, append bytes, finalize with source/status metadata, and resolve an immutable receipt. A receipt records artifact identity, captured size and completeness. Only the harness can finalize command-capture artifacts. Ordinary worker-authored files remain editable workspace state.

**Create blueprint:** proposed `bp-focused-agent-session-python`. Compose the six selected existing/extended leaves plus the new evidence store. No nested blueprint dependency. Reuse the laboratory runner's proven ordering and recovery design while exposing task-independent tool bindings.

Proposed consumer API: `FocusedAgentSession(reference, model, tools, policy, stores).run()` with resumable state and an inspectable result. Exact types are to be fixed before implementation. The worker tool surface exposes ordinary actions, bounded evidence inspection and explicit completion. A tool-free or empty model response must not silently count as a successful submission.

**Exclude from this slice:** an outer reference reviewer, adaptive compaction judge, additional agents, task-specific laboratory implementation, and a new UI. The worker chooses its scientific or engineering actions and completion time. The reference remains unchanged unless its owner supplies an explicit revision.

**Rules impact:** the implementation must qualify the cross-component receipt contract: persisted evidence before a usable receipt, bounded visible payloads, explicit incomplete capture, and resolvable artifacts after compaction. These are runtime contract checks. “Small purposeful turns” remains a behavioral qualification criterion, not a source-code lint rule. No rule or widget is changed by this design-only plan.

## Qualification and next experiment

First prove one end-to-end cycle: large tool output, decisive evidence outside its preview, targeted retrieval, coherent action, compaction, renewed retrieval and correct continuation. Exercise failed commands, long single-line data, exhausted capture quota and interrupted finalization. Check exact artifact bytes and the actual next model payload. Track generated reasoning as well as visible prose.

Use both deterministic interface checks and actual-worker qualification. A forced sequence of tiny calls is not evidence of useful self-pacing. The terminal qualification outcome is a correct result supported by retrieved evidence with preserved continuity, not a successful file write or a larger number of context windows.

After qualification, freeze the same focused harness for every arm of the compaction-threshold study. Measure final task correctness and total cost per attempt and accepted outcome. Controller comparisons can follow using the same qualified foundation.

This prioritizes a concrete harness capability while retaining evaluation throughout development. [Primary tool-design guidance](https://www.anthropic.com/engineering/writing-tools-for-agents) supports focused tools and direct behavioral evaluation; it does not validate this proposed implementation.
