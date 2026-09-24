# The app: a head of engineering's day

The brief is written like a document because the person holding it is the head of
engineering, and that is what they hold. The rest of the app follows the same rule: the
operator's day is documents crossing a desk, not dashboards. This note fixes what those
documents are, where each field comes from, and which runs to build against. It was
derived from reading four real runs end to end (`/home/Vinscen/mizpah-runs/`):

| Run | Use it for |
|---|---|
| `sales2/` (`sales/`, `sess/`) | the clean happy path — 8 work orders, 9 briefings, GO |
| `suite/underspec` (`underspec-sess/`) | three change requests awaiting signature |
| `suite/estimation` (`estimation-sess/`) | a controller stall — three briefings applying nothing |
| `landing1/` (`landing/`, `sess/`) | one bad work order: high class, 12 criteria, aborted; stopped by hand |

A renderer that turns any run into this paperwork exists as a scratch script (`inbox.py`,
this session); the sample output is what the screens below should look like in prose.

## Words

A **task** is the unit in the sidebar: one brief, its route, its map, its runs — a funded
piece of work with deliverables and a budget, in the JPL sense. Inside a task the route's
entries are **work orders** (Terra calls them tasks; the UI does not). A **project** is the
level above, which commissions tasks over time; it does not exist in the app yet. Terra's
own word for the task's directory stays `project` (`--project`, `.terra/`); that is the
engine's name for a folder, not the user's name for the work.

## Two kinds of surface

**Project views** are the standing documents of the project — you open them to look
something up. **Daily work** is what arrives — you open the app and it is there, in order,
and each item asks for one thing: read it, sign it, or go and look.

```
┌ status strip: elapsed · cycle · GO/NO-GO · "work order write_report in progress, 105 turns" ┐
│  DAILY WORK        │  BRIEF   ·   WORK ORDERS   ·   DATA BOOK                    · style     │
```

Daily work is the landing tab. The project views are the other three.

## Daily work: the inbox

An inbox is one column of documents in arrival order, newest at the top, each with a
header block (project · date · from · status) and a one-line summary. Opening one shows
the full document. Unread and awaiting-signature items are what the badge counts.

Arrival order is recoverable from the engine: the cycle order is fixed
(route → task → eval → task → eval …), `route.json` stamps every task with `updated_at`,
and `brief.json.proposals[].created_at` stamps change requests. Evals carry no timestamp
of their own; the briefing that follows task *i* takes task *i*'s close time.

### Daily Briefing — from Project Eval, one per cycle

Source: `sess/loop.json` `cycles[].route` (the opening one) and `cycles[].evals[i]`.

- **Readiness**: `GO — brief answered` when `done`, else `In work — the brief is not yet answered`; a cycle that applied nothing while work is owed stamps `NO ACTION` in red. The gate
  stamp lives here; it never gets its own page.
- **Situation**: the `why` sentence verbatim. Across a run these read as a narrative
  ("the map has shown the inputs but not the readings…" → "…the gate is green").
- **Actions taken**: `applied` as prose — "Minted 3 unknowns: …", "Routed work orders: …",
  "Filed 3 change requests", "Re-classed …". Empty applied is written out: *none — nothing
  new owed*. Three of those in a row is the stall; the strip goes amber on the second.
- **Declined**: `refused[]`. This is the part the operator actually reads, and it is
  long (13 in sales2 briefing #2, 26 in one weather briefing). Group by shape — the
  refusal strings share prefixes ("an artifact unknown is an agreement, not a label",
  "names no known or unknown its content must agree with", "is neither minted here nor
  open") — show the count per shape, expand to the list.

### Work Order — from the worker, one per task, arrives on close

Source: `route.json.tasks[]` joined with `sess/tasks/<id>/result.json` and
`loop.json.cycles[].tasks[]`.

- Header: **effort class** as a word with the bucket and points in the margin —
  IMPLEMENT (low, 3) · VALIDATE (medium, 8) · EXPLORE (high, 21). **Hours charged**:
  `turns` of `turn_budget`, estimate `turn_estimate`. **Status**: CLOSED / BLOCKED /
  ABORTED (verdict complete / blocked / error).
- **Acceptance criteria**: the `acceptance[]` unknowns with their `claim` text. Fall back
  to the loop entry's `unknowns[]` when the route's list is empty (`write_report` in sales2).
- **Close-out**: `evidence[-1]` — each known with value, unit, type, n, confidence, and the
  run ids. A work order with no evidence and status CLOSED is wrong on its face.
- **Stamps** down the margin: `rounds[]` — turn N: EFFORT PAUSE (`gate: "effort"`),
  GATE RED (`false`, with the first problem), GATE GREEN (`true`), METHOD FILED
  (`"playbook"`). This is the whole story of a task in four lines.
- **Worker's note**: `final_text` of the last round whose gate is not `"playbook"`
  (the playbook round's text is just the procedure slug).
- **Method**: `playbook.installed` (procedure filed), `playbook.ignored` (drafted, not
  accepted), `widgets.checked_in`. The flywheel is invisible today; this is where it shows.
- **Anomalies**: `overruns`, `handoffs`, and from `events/session.jsonl` the counts of
  `tool_rejected`, `tool_repeated_call`, `interjected`; `problems[]`; `verdict: error`.

landing1's `build_page` is the reference bad work order: EXPLORE, 12 criteria, ABORTED,
no turns recorded, close-out empty. It should look bad on the page without a chart.

### Change Request — from Project Eval, arrives with the briefing that filed it

Source: `brief.json.proposals[]`. The engine already numbers them `CR-001`.

- **Finding**: `summary` before ` — evidence: `. **Evidence**: the part after.
- **Proposed amendment**: the `patch` keys as lines (add need / add deliverable /
  add non-goal). It is a set of additions, not a diff.
- **Signature line**: ACCEPT (brief → v+1) · REJECT. Accepting bumps the brief version.
  Per the GUI conventions the memo opens over the brief without a prompt; the draft stays
  live behind it.

Note the same three CRs came out of two underspec runs shaped differently: in the stalled
run `add_need` copies the existing need text; in the clean run it is a genuine new
data-source need. The signature UI should make a bad amendment easy to reject.

### Anomaly Report — from loop ops

Sources: `sess/outages.jsonl` (model outages — "Ornith server stopped 08:15Z" was one),
`loop.json.stop == controller_stalled`, health events. These arrive as documents, not
toasts. The estimation run ends with one.

### Closing Report — from loop ops, once

Source: `sess/report.md` plus `loop.json` `{stop, tasks_run, hours, open_proposals}`.
The run card: stop reason, engine sha, model, hours, the work-order list with turns,
infrastructure, and (fixture runs only) the score. Score is never a pane of its own.

## Project views

- **Brief** — as built. Change requests are reached from the inbox and from a badge
  here; they are amendments to this document and open over it.
- **Work Orders** — the route as a register: every work order's header row (class,
  status, turns, criteria count), open ones first; click to the document. Same document
  as in the inbox, standing instead of arriving.
- **Data Book** — the map pivoted by the brief: one row per need and deliverable with
  the known that answers it (value, unit, type chip number/boolean/label, n, confidence,
  methods), then unknown → probe → runs beneath. Each known is a test report: claim,
  method (`probe_ids`, `probe_source_sha256`), `stats.by_run[]`, `adopted_from.map`.
  Runs (`runs/<id>/meta.json`: `measures[]`, `artifacts[]`, `from`) are the appendix.
  Labels (`S03`, `orders/2025-W13.csv`) read as results here; they would not in a grid.

## Home: the Deputy's office

The frame is the NASA Administrator's: the person says what they want done and signs; the
**Deputy** (as in Deputy Administrator — chosen 2026-09-20 over "chief of staff", SpaceX's
bot, and "technical assistant", too junior) writes the brief. Scope, decided the same day: a
**brief formulator and nothing else** — you talk to it on Home and a brief appears in Drafts
for your signature. The wider office (morning read, carrying change requests, explaining
runs) is out until this one duty is good. `docs/DEPUTY.md` is its job description.

```
┌ HOME ───────────────────────────────┬───────────────────────────────────────┐
│ DEPUTY  [grok-4.6 · high ▾]  8 ON RECORD │ ON THE DESK · gyms/ornith-landing  × │
│ ▸ Drafted and shown: five needs,    │  Ornith landing page        DRAFT     │
│   one deliverable…                  │  Mission …                            │
│   start a landing page for ornith,  │  Needs  1 Know the number of …        │
│   budget a weekend                  │  … (editable in place, Ctrl+S)        │
│ > _                                 │  ISSUED BY ______  [SIGN AND ISSUE]   │
└─────────────────────────────────────┴───────────────────────────────────────┘
```

Left: the record — the person's lines in ink, the Deputy's with a `▸`, the engine's notes
small. While a turn runs, every step shows under the working mark, off the session journal
(`engine/deputy_activity.dart`, `readDeputySteps`): each model call with its time, each tool
call with its command and first line of output, a compaction named as such, the open step
counting up; after the reply the same list folds to "N STEPS · N TOOL CALLS · N s" under it.
The one-line "what it is doing this moment" (`readDeputyActivity`, the engine's `pending_io`)
and the stop button are the other session's, and stay. Scrolling: on send the message pins to
the top of the window with a small margin (`topMargin`), the work streams in below, and the
foot follows once it overflows unless the person scrolled; a trailing space (a window's
worth) is always under the list so the last message can reach the top and nothing moves when
the reply lands. A line the seat never heard (the send failed) goes back into the box. The Deputy's replies
render as Markdown with LaTeX (`gpt_markdown`; `\( \)` / `\[ \]` for maths, `$` left alone
because budgets say "$"); the person's own lines stay plain. The header carries the seat's model as a chip (`widgets/model_pick.dart`): the models
chosen lately (`AppSettings.recentModels`, plus what the seats run on now), then "See all
models…" into the Providers page. Changing the model mid-conversation is a
`FocusedSession.rebind` (harness blueprint 0.31.0): the new model takes the seat and, if the
window holds work, first writes the working memory itself (a forced rollover); the record
gets one line and nothing is archived.

Right: the paper the Deputy pulled up ("look at this") — the brief document itself, editable,
ending in the signature line. A draft is a gym whose brief is in draft status; it sits in the
sidebar under DRAFT like any other. Signing furnishes it in place (or moves it into a named
repository), issues the brief and starts the loop; discarding is the Deputy's (`draft_discard`).

Environments are the Deputy's to set up (user, 2026-09-21: "it sets up the gyms" — a gym's
environment is part of that; a toolchain like a Lean verifier is downloaded once and every gym
in that environment gets it mounted). The Deputy's one shell is for exactly this: `/work` is the
environments directory (`bases/`), the package hosts are reachable, the gyms are not mounted,
and `terra`/`mizpah` commands are refused there. `environment_new` makes `/work/<name>` with
its record; bash builds it (venv, `tools/`, `bin/` wrappers relative to their own location,
shebangs rewritten to `/usr/bin/env python3` so the directory works at its mount path);
`environment_finish` writes the note the worker reads and the `$BASE` env and runs
`bases.check` (relocatable, has a note). Verified: a venv + package + wrapper environment in
33 s of Deputy time, usable from its host path. Every gym has an environment: `bare` (Python and
a shell, `bases.ensure_bare`) exists on every machine, and `bases/default.json` names the
**default** a gym gets when none is named (`mizpah.bases default [name]`; the sheet's
environment dialog marks it and can set it). The loop-built path (`draft new --builds`, the
`mizpah-build-environment` procedure, `bases.adopt` on green) remains for a brief whose
deliverable is an environment, but the Deputy does not use it.

Engine: `mizpah.deputy say|status|reset` (state under `<state>/deputy/`: the session,
`turns.jsonl`, `showing.json`), `mizpah.draft new|show|discard|authorize|list`,
role `deputy` in `mizpah-provider use --role`. The system prompt is composed from
`prompts/deputy/` (its `order.txt`: the shared glossary, the Deputy's words, brief, environment, policy) and re-read on every turn: `rebind` swaps
a revised text into the standing session's system message, no rollover. The Deputy's draft tools are
host verbs (`draft_new`, `draft_write`, `brief_show`, `draft_discard`, `environment_new`,
`environment_finish`): Python callables bound to the session at open time and run in-process
(blueprint 0.32.0: a command tool with `host: true` plus `handlers=`), journaled like any tool
call, milliseconds each; a full brief is two verbs and the turn is otherwise the model's own
generation. Its only shell tool is bash, scoped to the environments directory (above). The desk is read
off the session journal (successful `draft_show` / `draft_discard` calls), never off the
model's words.

## Status strip

Mission clock, not dashboard: elapsed, cycle, GO/NO-GO, and one sentence — "work order
`write_report` in progress, 105 turns" · "3 change requests await signature" ·
"stalled: two briefings applied nothing" · "stopped: nothing owed". Source: `loop.json`
plus the current task's `result.json`; stall = consecutive evals with empty `applied`.

## Not yet seen in any run (design the slot, don't spend on it)

- Controller check-ins: every task has `checkins: 0` and empty `held_guidance`.
- Re-bucketing: `rebucket` is always `[]`.
- A red gate at the loop level: `terra gate` was green in every finished run; landing1's
  in-progress task is the nearest real red.
- `loop.json` schema drift: landing1's aborted task lacks `turns`, `checkins`,
  `unknowns`. Parsers must tolerate missing keys.


## Sharing the desk (2026-09-20)

The desktop app is also the server. `Settings › Sharing › On` starts an HTTP +
WebSocket server inside the running app (`lib/engine/rpc_server.dart`) that
serves the engine seam (`Engine` and `ProviderClient`, every method by name,
typed on the wire by `lib/engine/wire.dart`) and the built web app from
`app/build/web`. A browser on the tailnet loads the page and talks back to the
same origin through `RemoteEngine` (`lib/engine/remote_engine.dart`). The two
worlds share one `boot()` (`lib/boot/`): on the desk it constructs `LocalEngine` (was `FakeEngine`)
in-process; in a browser it constructs `RemoteEngine`. Settings, the read/
dismissed marks and the signature image live on the desk and are read through
the engine, so a browser and the desk agree. A token minted on first enable is
part of the address; Tailscale is the fence around the port.

Build the page once per UI change: `cd app && flutter build web --release`.
The desk hot-reloads; the iPad sees what was last built.
