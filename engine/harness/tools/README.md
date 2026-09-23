# Focused worker/controller harness

The worker carries out the assignment. A separate controller receives the reference,
maintains a living account of the full project, and corrects a supported mismatch
with the reference. The controller assumes competent worker execution and leaves
aligned work alone. The worker writes its own handoff when its context fills.

The supplied configurations ask the worker to write working memory organized around
the current objective and constraints, established state, unfinished work, the
continuation point and essential references. The prompt distinguishes observed results
from claims and plans, preserves prerequisites, and reconciles earlier notes with new
evidence. It requests plain Markdown without copied message envelopes or nested handoffs.
Edit `session_policy.handoff_prompt` to change this instruction for new sessions.
Resuming an existing session retains its saved prompt. These are model instructions;
the harness does not verify the factual quality of the resulting memory.

Generation allowances accept `-1` for unrestricted output, subject to physical
context and the independent attempt deadline. `output_headroom_tokens` reserves
input space without imposing an output-token cap. Reasoning budgets are supplied
separately in generation settings. Every worker reset archives its original window
in the workspace and retains the completed action still awaiting follow-up. If an
oversized completed tool batch prevents a handoff from fitting, dynamic fields are
explicitly excerpted with archive references. Fixed inputs
that still cannot fit produce a persisted `blocked` state. Large controller
observations likewise use bounded excerpts with `history_read` pointers; their raw
completed-turn records remain intact. Every controller request is fitted again
after tool results arrive. Its current project document is refreshed, recent
complete exchanges are preferred, and older raw review messages remain retrievable.

The controller's project document contains nested todos and ordinary notes: where the
project is going, where it is now, what has been established, what remains, and why
important decisions were made. Completed work and useful failed approaches remain
available. The model chooses and revises the outline; there are no mandatory narrative
fields. Entries stay at project-trajectory level: milestones, consequential findings,
decisions, blockers and changes of direction. Routine activity stays in the traces.
Related actions are consolidated into outcomes, and the document need not change on
every review. The reference remains separate and fixed.

## Controller reviews

Reviews start after worker turns 1, 21, 41 and so on, with the latest 10 completed
exchanges. A final answer receives an additional review by default. A parallel tool
batch counts as one worker turn only after every result is available. Controller
calls and worker handoff calls do not advance this cadence.

Use the supplied traces first. Inspection and checks are optional: when the traces
provide enough information, the controller can update its document as needed and
return its decision directly. Each review has these tools available:

- `project_read` and `project_edit`: read the project document or apply an exact unique
  text replacement at its current revision. An empty anchor initializes an empty document.
- `project_edit_range`: replace a half-open character range at its expected revision.
  Edit failures return bounded current text and revision for recovery.
- `workspace_list` and `workspace_read`: inspect files in the worker snapshot.
- `history_read`: retrieve any inclusive range of completed worker turns, including
  turns that have left the recent window. Results contain applied input, visible output,
  native tool calls and outcomes; internal reasoning stays in the raw audit.
- `review_history_read`: retrieve original messages from the current review using
  zero-based inclusive message indices and character pagination.
- `run_check`: execute a command in a disposable isolated copy of the worker workspace.
  Every check starts from the same snapshot. All file changes are discarded; combine
  dependent setup and checking in one command.

Document/file/history reads include character-based continuation offsets. Only the
first document page enters the initial review prompt; the controller can read more.
The sample configuration allows a 256,000-character document and 24,000-character
pages. Model-call and tool-call limits are `null`: the controller chooses when its
investigation is complete. Positive limits remain supported for explicitly bounded
configurations. The controller input target is 48,000 tokens, with two recent exchanges
preferred during fitting. This target is a working allowance; fixed inputs may exceed
it while still respecting physical capacity and output headroom. Each request is
checked with the endpoint tokenizer. Run autonomous sessions under an independent
process deadline, as the pilot does. Deadline or capacity failures never imply approval.

The controller edits its document through tools, then finishes with JSON containing
`correction`, `evidence` and `warrant`. `"None"` holds current guidance; `""` withdraws
it; other correction text replaces it. Changes require evidence and justification.
The guidance appears once in each next worker request. At completion, replacement
resumes work; holding or withdrawing accepts the answer. These remain model judgments.

## Configure and run

Copy `config.example.json` and set worker/controller URLs, model IDs and capacities
for the intended loaded models. The sample URL and `local-model` are placeholders.
Set `controller.policy_file` relative to your configuration or use an absolute path
to `controller.md`. The server must implement the configured chat, template and
tokenization endpoints, native function calls, and explicit token usage. Final
controller JSON is validated locally. The two roles may use separate servers.
Rejected complete decisions receive explicit validation feedback in the same review.
The raw response and consumed call count are preserved, and the worker stays paused
until a decision passes. An explicitly configured call limit remains a failure boundary.

From the repository root:

```sh
python3 tools/focused-harness/run_session.py start \
  --config /absolute/path/config.json --root /absolute/path/new-session \
  --assignment /absolute/path/assignment.md --reference /absolute/path/reference.md

python3 tools/focused-harness/run_session.py status \
  --config /absolute/path/config.json --root /absolute/path/new-session

python3 tools/focused-harness/run_session.py resume \
  --config /absolute/path/config.json --root /absolute/path/new-session

python3 tools/focused-harness/run_session.py project \
  --config /absolute/path/config.json --root /absolute/path/new-session

python3 tools/focused-harness/run_session.py export \
  --config /absolute/path/config.json --root /absolute/path/new-session \
  --output /absolute/path/new-workspace.tar
```

Use `--project /path/project.md` on `start` to seed controller memory; otherwise the
controller creates its document. The `project` action prints the current document or
writes a new file with `--output /path/new-project.md`. The authoritative document is
versioned with session state. Editing an exported file does not modify a saved session.
Use `--initial-workspace` to supply an explicit worker-visible tar archive; otherwise
its workspace starts empty. Omit `--reference` to bypass the controller.

`--pause-after N` pauses after N additional worker turns at a resolved boundary. There
is no default total-turn limit. Resume preserves original prompts, generation settings,
budgets and model/shell bindings, including an unfinished controller investigation.
Configuration changes do not rewrite saved settings. The document and held guidance
survive worker resets and process reopen; partial tool batches resume at the next
unresolved tool. An uncertain dispatched operation reports `blocked` and requires
inspection of the journal. No automatic replay or generic recovery command is supplied.

Saved schema 1 sessions from the initial four-field implementation require their
original harness. This version uses schema 2 and rejects old sessions explicitly;
it does not overwrite their project history or silently reinterpret their settings.

## Isolation and records

The Linux shell uses bubblewrap and the user's systemd service manager with explicit
resource limits. Worker files in `/work` persist; processes do not persist between
commands. Controller checks use the same isolation but discard the outgoing workspace.
Host project files, session databases, reference files, GPU devices and host model
services are not exposed to these commands.

The session root holds `state.sqlite3`, append-only `events/`, content-addressed
workspace snapshots under `workspaces/`, and disposable storage under `scratch/`.
Host records include private reference/project text and raw model exchanges. These
records and snapshots grow as a session runs. Complete journal checkpoints can finish
interrupted database commits on reopen. The document is model-maintained and fallible;
format/revision validation does not prove its completeness or truth.

## Qualification

`python3 tools/focused-harness/qualify.py` uses a scripted loopback model endpoint,
the actual launcher and the real OS sandbox. It checks worker persistence, restart,
repeated handoffs, multi-call controller reviews, historical evidence retrieval,
project-document updates, private-state separation and discard of check modifications.
Results are saved in a new `artifacts/focused-harness/qualification/<timestamp>/` folder,
including the resulting `project.md`, request captures and summary.

Tests also cover interrupted controller checks, restart partway through a tool batch,
stale and range edits, large-document paging, optional limits, malformed decisions,
controller bypass, reviews exceeding eight model and sixteen tool calls, and retrieval
after context reduction.
Synthetic responses and token counts qualify execution mechanics. The first live
pilot and its limitations are recorded in
[the original report](../../artifacts/focused-harness/gemma4-pilot-20260916/REPORT.md).
The approved reliability revision follows
[the fix proposal](../../artifacts/focused-harness/gemma4-pilot-20260916/FIX-PROPOSAL.md).
Fresh live qualification and a new versioned pilot are required before judging its effect.

The 2026-09-16 qualification passed: 33 tests, 22 worker commands, three disposable
controller checks, seven handoffs and separate-process resume. See the
[run summary](../../artifacts/focused-harness/qualification/20260916T163936.284392Z/summary.json)
and [resulting project document](../../artifacts/focused-harness/qualification/20260916T163936.284392Z/project.md).


## Native client adoption, 2026-09-16

The example selects `provider: llama_client` for both roles.
Worker, controller and handoff requests now use the reusable native streaming client.
The known-issue policy detects repetitive text, reasoning and tool arguments, and
sequences of eight identical completed tool calls. A detected repetition closes the
upstream connection and rejects the entire response before any partial tools execute.
The harness allows two explicit retries with brief recovery feedback; rejection counts
survive restart. Uncertain network failures still block rather than replay automatically.
Sampling penalties are unchanged. Configurations without a provider retain direct JSON
transport for historical compatibility.

The installed versions are llama client 2.1.0, persistent model session 1.4.0 and
focused session blueprint 0.5.0 at that release; see the reasoning-retention revision below. Their release passed 193 component checks, native
live tool/token/reasoning/cancellation probes, and a small real worker/controller task.
The adopted launcher also passed scripted sandbox qualification with seven handoffs
and restart. See [release evidence](../../artifacts/focused-harness/client-reliability-20260916/README.md)
and [the completed frozen pilot audit](../../artifacts/focused-harness/gemma4-pilot-v2-20260916/REPORT.md).
The revised reference-alignment policy is installed; its effect on those benchmark
cases has not been tested. No new scored comparison has started.

## Reasoning retention, 2026-09-17

Persistent model session 1.5.0 adds `session_policy.reasoning_retention` with values
`all`, `latest` and `none`; the blueprint is 0.6.0. It shapes only the wire view of a
request: with `latest`, reasoning is sent for the final assistant message alone, so the
step in progress can continue while completed steps are read through their visible
content, calls and results. Stored messages, the raw model audit, workspace archives,
`history_read` and `review_history_read` keep every reasoning field. The worker, its
handoff, and the controller's own review loop all use the same view; the controller's
worker evidence already excluded reasoning. The leaf default is `all`, which reproduces
earlier wire behavior exactly; the supplied configurations select `latest`.

Motivation: in the v4 pilot the Gemma template re-rendered every retained reasoning
block, so 42% of the worker's final rendered prompt (25,346 of 60,797 characters in
drift-on) and about a third of the controller's bootstrap review context was completed
reasoning. This follows the convention of reasoning APIs that carry thinking only for
the current step. The scripted qualification now sends reasoning on every scripted
turn and checks that all 55 wire requests carry at most one reasoning field, on the
final assistant message, while the audit retains all of them
([run summary](../../artifacts/focused-harness/qualification/20260917T022616.664435Z/summary.json)).
Its effect on live task outcomes and cost is untested until a matched pilot runs with
this as the sole change from v4.

## Bounded worker actions, 2026-09-17

Committing to 60–90K windows commits the worker to small actions
([design note](../../artifacts/agent-control-theory/bounded-context-working-set.md#bounded-actions)).
Sandboxed shell execution 1.1.0 adds exact-occurrence text edits of workspace snapshot
members; the blueprint is 0.7.0. The worker now has three tools: `bash`, `write` (one
complete short UTF-8 file) and `edit` (exact `old_text` → `new_text`, which must match
`expected_occurrences`, default one, or nothing changes). Both file tools act on the
persisted snapshot without a shell process and record a `tool_outcome`.

`maximum_tool_argument_characters` bounds every worker call. A larger call is never
executed: the worker receives a structured `rejected` result naming the bounded route,
and a `tool_rejected` event is journaled. `maximum_write_characters` and
`maximum_edit_characters` bound the file tools' text. The supplied configurations use
2,000 / 1,500 / 1,200 characters and a worker system prompt that names the tools and
the bound. Settings default to `bash` only and no bound, so earlier saved sessions
resume unchanged.

Scripted qualification now runs a bounded-actions scenario through the real launcher
and sandbox: an oversized heredoc is rejected unexecuted, `write` and `edit` change the
snapshot, an ambiguous edit is refused with `occurrence_mismatch`, and a subsequent
`bash` observes the edited file
([run summary](../../artifacts/focused-harness/qualification/20260917T024907.518297Z/summary.json)).
Whether bounded actions change live task outcomes, total worker tokens or turn counts
is untested until a matched pilot runs; the continuity fixture, which mandates
unfiltered 40K packet reads, is a compaction stress test rather than a benchmark of
this design.

## Argument projection and reference-only corrections, 2026-09-17

Persistent model session 1.6.0 adds `session_policy.argument_excerpt_characters`. Once a
tool call's result is present in the conversation, long string fields of its arguments
are excerpted on the wire with a deterministic marker naming the original length; a call
still awaiting its result keeps full arguments, and stored history, archives and
`history_read` are unchanged. The projection lands one request after the outcome, at the
tail of the prompt, so the cached prefix before it is untouched. Motivation: in v7's
continuity arm the worker's context held nine copies of a rejected whole-file write
(23% of a 150K-character prompt), and it re-emitted that file 26 times.

Controller progress 2.3.0 adds `review_policy.execution_terms`. A replacement correction
containing a configured term (tool names, chunking, size limits; whole-word match) is
rejected at decision validation with feedback to state the unmet reference outcome
instead; holds and withdrawals are never blocked. `controller.md` now says the same in
policy: the controller corrects alignment with the reference, not mistakes of execution,
and never cites its own earlier guidance as a warrant. Motivation: 4 of the 11 live
corrections in v6–v7 prescribed bash chunking or tool choice. Blueprint 0.9.0 pins both.

## Concern-gated controller reviews, 2026-09-17

Blueprint 0.11.0. The controller's investigative tools (`workspace_list`, `workspace_read`,
`history_read`, `run_check`) are locked until it states one concrete concern through the
`investigate` tool, which is journaled as `controller_concern`. Each boundary has a budget
of investigative calls, `controller.investigation_budgets` (supplied configurations:
bootstrap 0, periodic 2, completion 6), and `controller.maximum_document_edits_per_review`
(2). The review envelope reports the review kind, the stated concern and the remaining
budget. Defaults of `null` preserve the earlier unbounded behavior. Motivation: in v8d a
periodic review used 34 model calls and 16 disposable checks to conclude "hold", and the
bootstrap review routinely ran 16–45 calls before the worker had done more than `ls`.
`controller.md` now describes the review as a glance over a colleague's shoulder.

Also in this revision: worker identical-call detection spans the last six calls, so an
alternating delete/rewrite cycle is counted; the worker prompt says the first write after
any delete is headings or stubs only; the execution-terms guard matches tool names only
when backticked or followed by "tool" (the plain verbs blocked a correct correction in v8d).
Scripted qualification exercises all three budget kinds through the real launcher.
