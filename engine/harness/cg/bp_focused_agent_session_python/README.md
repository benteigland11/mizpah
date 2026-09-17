# Focused agent session

`FocusedSession` composes a native worker conversation, an isolated shell workspace,
a controller investigation loop, a living project document, an event journal and a
revision store. Import its public API from `src.focused_agent_session`.

Create with explicit `SessionSettings`, worker and controller `ModelClient` bindings,
and a `SandboxedShell`. `run()` continues until completion; `run(maximum_worker_turns=N)`
pauses after N additional completed worker turns, resolving their tools and review.
`step()` advances one persisted boundary. `open()` verifies the original bindings and
resumes worker or controller tool batches. `workspace()` returns the worker tar archive;
`project_document()` returns the controller-owned Markdown/plain-text document.
`status()` reports phases, counts, document revision and any unresolved operation.

The controller document covers the full project lifecycle, with nested todos and
ordinary notes. Its outline is model-owned. Exact unique text edits require the current
revision and preserve surrounding content. Completed findings, decisions, future work
and evidence can remain together. Reviews receive a bounded first page and can retrieve
more; the document does not need to fit entirely in the initial review prompt.

Worker tools are selected by `SessionSettings.worker_tools`: `bash` always, plus optional
`read` (numbered lines from a 1-based offset, at most `maximum_read_lines`, default 200),
`write` (complete short UTF-8 files) and `edit` (exact text replacement that must match
`expected_occurrences`, default one, or nothing changes). Both act on the persisted
workspace snapshot without a shell process. `maximum_tool_argument_characters` bounds every
worker call: a larger call is never executed and receives a structured `rejected` result
naming the bounded route, with a `tool_rejected` journal event. `maximum_write_characters`
and `maximum_edit_characters` bound those tools' text. All three default to unbounded and
`worker_tools` defaults to `('bash',)`, preserving earlier sessions. The bound is the size
of an action the harness is willing to carry in context, not a repair of model output.

Controller tools are `project_read`, `project_edit`, `workspace_list`, `workspace_read`,
`history_read` and `run_check`. History exposes completed worker exchanges outside the
recent observation window. Workspace tools read the fixed review snapshot. Each check
runs in an isolated disposable copy; its returned workspace is never committed. Only
project tools can change controller memory. Native tool errors are returned for repair;
uncertain dispatched operations block automatic replay.

The controller finishes with validated correction/evidence/warrant JSON: hold, replace
or withdraw guidance. The worker receives its assignment, conversation, its own handoff
and one copy of held guidance. The private reference, project document and review
transcript are not inserted into worker requests. Cadence and all review budgets are
explicit settings; review calls and handoffs do not count as worker turns.

Every model request is template-rendered and token-counted before dispatch. Reviews
have optional explicit model/tool budgets and cannot silently accept a result on exhaustion. Saved
checkpoints cover the active review conversation, pending tools, document edits and
worker workspace. Complete write-ahead checkpoints recover interrupted database commits.
No-reference sessions bypass the controller. Saved schema 1 sessions require the original
harness; schema 2 deliberately refuses to reinterpret their four-field state.

Dependencies are pinned in `blueprint.json`. Tests cover repeated worker resets,
process reopen during reviews, old-evidence retrieval, document paging and edit conflicts,
check mutation discard, uncertainty, budgets and reference separation. The offline
example runs without network or shell commands. The launcher and real-sandbox
qualification live under `tools/focused-harness`.

`llama_model_client(endpoint, known_issues=...)` composes the reusable llama client
with native streaming, exact template counting and the same event journal. Use it
for both worker and controller bindings; handoffs reuse the worker binding. Saved
binding identity includes the provider and resolved known-issue policy without
authentication headers, so reopen rejects an accidental provider/policy change.

`SessionSettings.maximum_generation_retries` explicitly controls recovery from
repetition cancellation and defaults to zero. A rejected response executes no tools,
accepts no decision, and does not replace a worker window. The rejection and elapsed
cost are logged before a retry, with the consecutive count saved across restart.
Retry feedback is short and excludes the rejected raw text. Exhaustion raises
`GenerationRetryExceeded`. Successful requests clear their rejection streak.
Timeouts and uncertain transport outcomes retain the unresolved-operation barrier.
