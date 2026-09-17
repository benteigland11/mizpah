# Persistent working sets for 60–90K context windows

Design proposal, 2026-09-16. The committed operating range is 60,000–90,000 tokens per context window. This refines the [query catalog](query-search-schema.md) and [focused harness](focused-harness-plan.md). It changes the design, not the running experiment or installed implementation. Reduced long-term noise through compaction remains the study's motivating premise; this proposal specifies the continuity mechanism needed to use it.

The agent should resume a particular investigation with its useful understanding intact. The normal path is **reuse the active working set, refresh affected evidence, resolve one gap, act, and record what changed**.

## Four parts with different jobs

| Part | Persistent contents | What enters a model window |
|---|---|---|
| Fixed task reference | Owner-provided objective, constraints, acceptance criteria, and their revision | The unchanged operative reference and necessary tool instructions |
| Active working set | Current understanding, relevant query/answer revisions, working hypotheses, meaningful failed approaches, blocking questions, next action and verification | A compact task packet plus selected evidence |
| Query catalog | Reusable questions, retrieval recipes, findings, relationships and provenance across the project | A bounded set of matches when the active set lacks something |
| Evidence and event archive | Full tool captures, source snapshots, run records and completed action identities | Targeted excerpts and change receipts |

The working set is a named, versioned subset of the catalog and task state. It is not an automatic copy of every recently accessed file. Its brief account of the problem explains relationships and assumptions; its references make those statements inspectable. Hypotheses remain labeled as hypotheses. A failure record states what failed, under which conditions, and what would justify trying again.

A small project orientation record can point to known areas and entry queries. Build it from useful discoveries. Do not require an initial whole-project documentation pass, and do not load every area's description into every window.

## Normal operation and retrieval order

1. **Use what is already in the active packet.** A current answer or loaded source slice does not require another catalog search. The model can request a known query or evidence id directly.
2. **Resolve a known pointer.** Open the missing section, definition, test result, or artifact field at its recorded version. Return a compact change report if its current source differs.
3. **Search the query catalog within the current area.** Use this when no adequate pointer exists, a question changes, or the task moves to another area. Select useful findings into the working set.
4. **Run a focused source query.** Retrieve the missing evidence within an explicit scope, interpret it, and preserve a worthwhile finding.
5. **Broaden when warranted.** A missing target, contradiction, failed prediction, insufficient coverage, or new task area can justify wider discovery. State the gap being addressed; broader search remains available without becoming the automatic response to every reset.

This is a default route, not a mandatory five-call sequence. A known file can be read directly. An empty catalog does not require a pointless catalog call before every source lookup. The result contract can batch deterministic search and filtering, while keeping the returned evidence bounded and attributable.

Within a window, append new observations and short state changes. Do not rewrite the entire working-set narrative after each read. Commit meaningful changes at completed action or investigation boundaries: a hypothesis changes, a question is resolved, a failed path is ruled out, or the next action changes. The harness records mundane metadata automatically: tool identity, parameters, output handles, source versions, sizes, and execution status. The model supplies interpretation and task relevance.

At a boundary, a context-safe rendering can replace an obsolete loaded section with a pointer. Native tool-call/result identity and execution history must remain valid; do not edit an unresolved tool exchange to free space. Retain the full event archive independently of what is rendered into the next model window.

## Reuse based on changes

Every query run records its evidence footprint and coverage. The footprint must be appropriate to the kind of result:

| Result | Changes that require attention |
|---|---|
| Known definition or exact source passage | Target file content, identity, or symbol resolution changes |
| Search, inventory, or absence claim | Changes to any relevant file or membership of the searched scope, including additions and deletions |
| Behavioral explanation or derived claim | Changes to its declared supporting queries, source scope, assumptions, or relevant runtime state |

The harness uses a source manifest and indexed change records to find affected entries. Process changed files and changed scope membership instead of rerunning every stored query. For a saved search, updates must also account for newly matching files; recording only the previous hits is insufficient. An affected query may return the same answer after re-evaluation, which is a valid result.

Two states remain distinct: **recorded evidence is unchanged within its declared footprint** and **the answer is supported for the requested use**. Mechanical invalidation establishes the first. It cannot prove a semantic dependency was never missed. Queries with an incomplete dependency scope remain uncertain or use a broader conservative scope. Useful stale locations can still guide discovery without being asserted as current findings.

Record query-definition and parameter revisions, repository/branch identity, local edits, and external-state versions where relevant. If source-change observation has a gap, reconcile the affected source scope before reporting evidence as unchanged. That reconciliation may require a machine-side scan; it does not require sending the repository into model context. Do not rely on filesystem watchers alone to guarantee complete change detection.

## Bounded actions

Added 2026-09-17. Committing to 60–90K windows commits the worker to small tool
actions. If a window should hold roughly 30–40 turns before compaction, a turn has to
be about 1.5–2K tokens all-in: tool arguments, the bounded result, and the visible
response. This is the budget the window implies, not a separate preference.

The v4 pilot showed what unbounded actions cost ([controller-input audit](../focused-harness/controller-input-audit-20260916/PAYLOAD-SIZE.md),
[v4 records](../focused-harness/gemma4-pilot-v4-20260917/README.md)): heredoc rewrites of
whole files, 2–4K characters each and repeated three times per file, were 41% of a
ten-turn controller window; the previous result echoed as applied input was another
25%; retained reasoning was 42% of the worker's rendered prompt. None of that came from
large command output in the free-choice fixtures.

Consequences:

- Bound tool actions at the source. Arguments above the per-action bound are rejected
  with feedback naming the bounded `write`/`edit` route, so a whole-file rewrite is
  structurally unavailable rather than merely discouraged. Edits use exact unique
  anchors with explicit conflict, as the controller's project document already does.
- After a call completes, its arguments are evidence, not context: project them to a
  reference (`wrote ledger.py, 1,412 characters`) in the worker's wire view and the
  controller's observations, exactly as large results are already routed to files.
  Reasoning from completed steps is likewise not resent (session leaf 1.5.0).
- The continuity fixture mandates 40K unfiltered packet dumps. It remains a stress
  test of compaction mechanics; it is not a benchmark of this design, because it
  forces the worker to violate the bound.
- Bounded actions cost more turns. The compaction-window sweep predicts that many
  small steps in a short window is the low-noise regime, but that is a scalar-model
  result; a matched pilot must measure total worker tokens and outcomes, not only
  per-turn size.

## Window construction and compaction

Treat the configured capacity as the whole window. Count the actual rendered prompt with the serving model's tokenizer and template, including tools and message formatting. Leave explicit space for the next model output, bounded tool receipt, and checkpoint work. If those reserves do not fit, complete the current safe boundary and rebuild before requesting more work. The maximum generation setting must participate in this calculation.

The next window contains:

- The fixed reference and necessary tool definitions.
- A task packet: current understanding, consequential unknowns, relevant query ids and revisions, important failed approaches, next action, and how to check its result.
- A small selected set of current evidence excerpts needed immediately.
- A bounded tail of completed interactions when it supplies continuity not already present in the packet.

A useful initial qualification target is a rebuilt prompt of roughly 10–20K tokens, including the reference, tools, packet and initial evidence, leaving substantial room inside a 60–90K window. This is a proposed tuning range, not a measured optimum or permission to truncate an oversized task reference. Adjust the packet's evidence detail first; if protected content cannot fit, report that budget conflict.

Compaction proceeds as a commit and reconstruction:

1. Finish or durably record the status of the current tool operation. Ordinary rollover waits for linked results; interruption recovery uses recorded execution identity and must not blindly reissue a mutation.
2. Record the remaining meaningful changes from the current window into the working set. A short model handoff can preserve transient context not already captured.
3. Save a checkpoint containing the reference revision, working-set revision, query/evidence versions, source-change cursor, and execution boundary. Verify referenced evidence is resolvable and required fields survive.
4. Assemble and token-count the next payload. If it exceeds budget, remove optional excerpts or reduce detail; preserve the task reference, unresolved consequential uncertainties, and evidence access.
5. Switch context only after the checkpoint and the next payload are valid. On resume, reconcile source changes since the checkpoint, flag affected entries, and continue from the recorded action.

Rebuild stable findings from their records. Avoid repeatedly summarizing a previous prose summary into another summary. The small handoff captures the current transition; it is not the only copy of the agent's accumulated knowledge.

Do not require every window to complete a whole feature. Compaction can occur halfway through an investigation while preserving its next question and hypotheses. A reset does not itself authorize more searching or mark progress.

## Example continuation

The worker is fixing a login failure. Its active packet contains the credential-check query, the route that invokes it, the relevant test, and a hypothesis about error handling. It edits the handler and runs the test. The harness saves the complete log and returns a short result plus its evidence handle.

The worker records that the original failure is fixed but a session test now fails. Its next action is to inspect the relevant log section and session boundary. After compaction, the new window receives that state and the same query ids. It opens the relevant section directly. It does not begin again with “find authentication in this repository.” If the handler changed outside the session meanwhile, the resume report points out that change and refreshes only the affected material.

## Qualification

At both ends of the committed context range, exercise a continuing task through multiple resets. Require the worker to recover a known answer without repeating broad discovery, preserve a rejected hypothesis, take the intended useful action, and retrieve decisive evidence outside the initial preview. Include useful new questions so success does not depend on a frozen script of known lookups.

Mutate a referenced file, add a new matching file, change an unrelated area, and simulate a missing change event. Check both missed invalidation and unnecessary invalidation. Interrupt checkpoint publication and a tool operation; verify the last valid state remains recoverable and completed mutations are not replayed.

Inspect actual model payloads and measured token counts. Track context used immediately after resume, tokens and calls spent recovering orientation, repeated broad searches, stale-answer use, catalog/working-set maintenance cost, and final task correctness. Compare the same bounded tools and context range with ordinary notes. Measure total worker, retrieval and compaction cost. The behavioral target is continued progress with less rediscovery, not a particular number of saved queries or a tiny handoff.

## Feature

Resume an autonomous investigation across bounded context windows by restoring a versioned working set and refreshing only affected evidence. This is an extension to the proposed focused session and query catalog, not a separate research controller.

## Candidates

1. Typed continuation record, incremental updates, protected fields, and bounded rendering: universal, Python, existing summary leaf to extend.
2. Exact model-message accounting and native tool-boundary rollover: backend, Python, installed session leaf.
3. Section budgets and output/checkpoint reserves: backend, Python, existing planner leaf to extend.
4. Source change records, query footprints and affected-dependency traversal: infra and universal, Python, existing and already-planned leaves to extend.
5. Persistent working-set selection and checkpoint assembly: composition in the planned focused-session blueprint.
6. Per-experiment capacity, retrieval limits and grader: product configuration.

## Classify

| Responsibility | Kind | Owner |
|---|---|---|
| Continuation record and rendering | Widget | Pure summary/continuation schema |
| Budget accounting | Widget | Counts, protected sections, required reserves and explicit fit result |
| Source identity and impact | Widgets | Change observation, declared footprints, affected records |
| Session continuation | Blueprint | Compose saved state, query tools, budget planner, native session and journal |
| Chosen capacity and evaluation | Glue | Supply the task and experimental configuration |

## Search

Project and configured-registry searches covered working sets, context packets, budgets, handoffs, source staleness and persistent-session blueprints. Library/registry results were deduplicated. Source inspections establish:

- Installed `backend-persistent-model-session-python` 1.1.0 counts the actual template through the serving endpoint, validates input plus output capacity, preserves tool identities, and refuses rollover over pending tool results. Its rollover currently uses base messages, a handoff string, and recent results. The composition must supply the new packet and checkpoint discipline.
- Library `backend-context-budget-planner-python` 1.0.0 accepts caller-provided section counts and reserves output. Its trim implementation treats a configured maximum cap as a minimum retained amount, so its trim plan cannot be adopted as a fit guarantee. Use the existing accounting foundation and correct the cap/floor distinction before relying on automated trimming.
- Library `universal-agent-compaction-summary-python` 1.0.0 has handoff prompt and metadata helpers. It clips source text by character count and normalizes record fields; it does not validate referenced evidence, guarantee token fit, or maintain a structured working set. Extend this leaf rather than introducing another parallel handoff format.
- Library `infra-artifact-dependency-staleness-python` 1.0.0 computes transitive impact over a supplied graph. Its full pass/fail contract expects impacted artifacts to change bytes, which is inappropriate for a refreshed query whose correct answer remains the same. A generic impact-only API is the reusable portion to expose.
- Library `universal-agent-operating-state-python` 1.0.0 tracks modes and nested workflows, not investigation knowledge. Installed `universal-decision-state-python` is tied to project/capability/inquiry transitions. Neither is selected for this continuation record.
- `bp-llm-context-bundle-python` 0.1.1 builds provider-neutral message bundles and keep/summarize/drop projections over app-owned conversation records. It does not own persistent query working sets or the current exact-template native-session protocol. Its inspected summary leaf is reusable; the complete parallel message-bundle assembly is excluded from this slice.

## Improve, Install, Create, Blueprint

**Use as-is:** the installed persistent model session, tool loop and event journal. Preserve the session revision-store API; the catalog's separately planned keyed API remains necessary for query records.

**Install and improve:** `universal-agent-compaction-summary-python` for a typed, versioned continuation record and incremental updates, evidence/query references, explicit hypotheses/gaps and bounded rendering. Preserve its legacy free-text summary API. `backend-context-budget-planner-python` for distinct floors/caps, protected-field handling, multiple required reserves, and an explicit unachievable-fit result. Exact final payload token counts remain authoritative.

**Install and improve:** `infra-artifact-dependency-staleness-python` with an impact-only public API separate from its artifact-regeneration contract. Compose it with query-specific footprints owned by the planned `universal-saved-query-state-python` and source-version/change capture owned by the planned `infra-agent-evidence-store-python`. File hashes alone do not provide a complete change ledger; add scope membership and reconciliation to the latter's proposed contract.

**Extend planned blueprint:** `bp-focused-agent-session-python` composes the continuation record, budget, impact, session and storage leaves. The query catalog remains a separate feature registered through ordinary tools, without nested blueprints. The model chooses relevant evidence and useful actions; deterministic machinery enforces persistence, provenance and capacity. No additional new leaf is proposed here.

**Rules impact:** qualify exact budget fit, resolvable references, source-change coverage, and no rollover across unresolved native tool calls. Avoid replacing semantic judgments with arbitrary turn-count rules. This document installs no widgets and runs no new agent experiment.

## Grounding

Stored references, selective retrieval and structured notes have precedent in [Anthropic's context-engineering guidance](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents). This proposal makes a persistent active subset and incremental freshness checks explicit. Their behavior and cost in this harness still require the qualification above.
