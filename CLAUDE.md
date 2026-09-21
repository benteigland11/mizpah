# Mizpah

Mizpah is a self-improving engineering loop for domains where the answer cannot be
verified but the method and the evidence can. Three forked tools do the work; a harness
with a controller/worker split drives them; a Flutter app is where the user holds the
reference. This file is the orientation for a fresh session. Read it, then
`prompts/00_glossary.md` — the words every seat and every design conversation uses, one meaning
each; use them in that sense here too — then `docs/DESIGN.md`, then `docs/lineage/LINEAGE.md`
if you need the evidence behind a rule.

## The loop (agreed design, 2026-09-17)

```
Brief ──► Unknown ──► Route ──► Procedure ──► Probe+run ──► Known ──► Map ──► Gate
  ▲          ▲                      │             ▲                            │ green
  │          │                      └──► Widget ──┘                            ▼
  └───── Project Eval ◄────────────────────────────────────── Procedure (improve / mint)
                                                                 (red: back to executing)
```

- **Brief** (Terra) is the reference `r`. Needs, non-goals, deliverables, enablers,
  phases, budget. Read-only to every agent; it moves only by `propose → accept`, and the
  user accepts.
- **Map** (Terra) is the persistent working set `y`: unknowns → probes → runs → typed
  knowns with n/confidence, staleness cascade. Separate file tree, separate write path
  from the brief. Never merge them (that is how the harness's reference got contaminated).
- **Unknowns** are the error signal `r − y`, made discrete and nameable. The controller
  mints them; each must cite a brief need or deliverable id, or it is refused.
- **Route** (Terra) is the only interface between controller and worker. A task carries
  the unknowns it resolves and a bucket; nothing about method. A bucket is the *mode* of
  work, priced in points: **low** (3) implement — path known, no search space; **medium**
  (8) validate — a couple of options considered, then conclude; **high** (21) explore —
  parallel exploration of several options. The numbers mean nothing without the mode; the
  controller buckets by how much is unknown about the method, the worker reads it as how
  wide to look.
- **Procedure** (Playbook) is the method. The worker's first act on any task is
  `playbook search`, then `start` — one step at a time, never the whole procedure.
- **Widget** (Cartograph) is the instrument a probe calls; a widget is never itself a
  finding. Several widgets behind one API is a blueprint.
- **Probe + run** (Terra) is the measurement. Claims become readings; a run is the only
  thing a worker may cite when graduating an unknown to a known.
- **Gate** (Terra) is mechanical pass/fail over brief + map. Nobody argues with it.
  Green is the verification signal for the *method*: only after green may a worker
  write or improve a procedure in the playbook. Red routes back to execution.
- **Project Eval** is the controller's judgment step: sitrep in; new unknowns and brief
  proposals out. It never sees the worker's transcript and never instructs the worker.
  It starts in a supervised mode (user reviews its outputs) and earns autonomy by
  evidence (e.g. N proposals accepted unchanged → auto-accept that class).

Controller owns: Brief, Unknown, Route, Project Eval. Worker owns: Procedure, Widget,
Probe, Known, Map writes. The controller does not use Playbook.

- **Deputy** (2026-09-20) is the third seat: the Administrator's brief formulator, one
  session kept between conversations (`engine/mizpah/src/mizpah/deputy.py`, Home in the
  app). You talk to it; a draft brief appears in Drafts for your signature. Drafts are its
  whole writable world (its `/work` is the gyms root with issued gyms bound read-only); it
  never issues, starts a loop, signs, or touches a live project. Scoped to that one duty on
  purpose. Job description: `docs/DEPUTY.md`.

## Rules carried from the harness campaigns (do not relearn these)

These came out of ten single-seed campaigns on a 26B MoE model in a 60K window
(`docs/lineage/campaigns/`). They are constraints on how we build, not suggestions.

1. **Small windows imply small actions.** Prevent large tool payloads at the source
   (argument bound ~2000 chars, write/edit ~1500, read 200 lines), then project what
   was rejected. Do not try to fix it on the input side afterwards.
2. **Teach a method, never a number.** Progressive refinement: skeleton first, then one
   piece per edit; prose by headings then sections. Never replace a block in one call —
   delete it (`sed -i 'a,bd'`) and rebuild by refinement; after any delete the first
   write is headings or stubs only. The system prompt must not contain numeric targets.
3. **Tools are what Claude Code exposes**: bash, read, write, edit. Deletes go through
   bash. Anything truncated goes to a file and returns the path; the model reads it.
4. **Reasoning is not retained** across turns (standard convention). The controller does
   not see it either.
5. **The wire view is verbatim for everything applied.** Only rejected oversized
   payloads are projected to a first line + file path. Projecting applied edits erased
   the worker's working memory (v8c). Tail-only rewrites preserve the prefix cache.
6. **More compaction, less long-term noise.** Retained history spans the prompt drift;
   noise ∝ that span. Pooled rms drift 2.1 (window 25) → 13.1 (no compaction) in the
   scalar model (`docs/lineage/compaction-window-sweep/`). Noise and reference error are
   separate levers.
7. **The controller catches misalignment with the reference, not mistakes of
   execution.** Corrections are reference-only, enforced structurally (`execution_terms`
   guard on backticked tool names). Engineering errors (a sqlite DDL mistake, three
   campaigns running) are not its job.
8. **Corrections state the delta, not the requirement.** v10 continuity: a correct
   catch ("packets 04, 08, 12 missing") phrased as "read all twelve in order" was read by
   the worker as *start over*; it wiped the file. Say what is missing and what to keep.
9. **The controller reviews like a person glancing over a shoulder.** Investigation is
   gated behind a stated concern with per-boundary budgets (bootstrap 0, periodic 2,
   completion 6); ≤2 document edits per review. Unbudgeted it re-did the work (34 calls,
   16 checks, "aligned").
10. **Empty model responses retry** through the generation path. A reference that is
    under-specified is a fixture bug, not a controller failure — fix the reference.
11. **Cadence 20 / history 10** and relay/deadband corrections were the endpoint of the
    control study; integral input correction handles slow drift.
12. **The 90K-token prompt killed the local server three times.** 60K is the operating
    point for that model.

## Where things live

- `prompts/` — what the seats are fed, as small Markdown files composed into prompts: the
  glossary (`glossary.md`), blocks that say how one thing works (`unknowns.md`,
  `terra_philosophy.md`), and each seat's policy. One file, one thing; see its README.
- `design/` — our notes on how a mechanism is meant to work (`gate.md`, `handoff.md`), for
  the people designing the loop; never fed to a seat.
- `engine/terra`, `engine/cartograph`, `engine/playbook` — forks (source, `cg/` widgets,
  tests). Upstream commits in `UPSTREAM.txt`. MCP servers and plugin manifests were
  dropped; the harness couples the tools directly in Python.
- `engine/harness/cg/` — the focused-agent-session harness and its widgets
  (`persistent_model_session` wire view, `controller_progress` review policy,
  `sandboxed_shell_execution`, `session_event_log`, `llamaclient`). `engine/harness/tools/`
  has the campaign runner (`run_session.py`, `pilot.py`, `qualify.py`), the controller
  policy text (`controller.md`) and the v10 configs (`config.gemma.json`).
- `engine/mizpah/` — the new loop (controller + worker roles over the three tools).
  Empty at fork time; this is where the work is.
- `app/` — Flutter desktop. Launches the engine as a sidecar, renders brief/route/map/
  gate, holds the proposal queue and the autonomy dial.
- `docs/DESIGN.md` — the loop in prose with the open seams. `docs/lineage/` — the
  design and evidence documents this grew out of, with pointers back to the research
  workspace `/home/Vinscen/decisions` (see `docs/lineage/LINEAGE.md`).
- Design canvas (Flywheel Studio): https://claude.ai/artifact/47xMRDgkAUvhp5siPbw6jq —
  the agreed graph and answered questions live in its `design/main` document.

## Working here

- `uv sync --python 3.12 --managed-python`; everything runs from `.venv`. One workspace,
  one interpreter; the same environment ships as the app's sidecar.
- Tests per package: `cd engine/<pkg> && ../../.venv/bin/python -m pytest -q`. Harness
  widgets: `cd engine/harness && PYTHONPATH=$PWD:$PWD/cg/<widget> ../../.venv/bin/python -m pytest -q cg/<widget>/tests`.
- `cg` is a namespace package merged from terra, cartograph and harness; never add
  `cg/__init__.py`.
- Local model: llama.cpp Gemma-4-26B-A4B behind a manager on :58000 (`/start`,
  `/status/58081`); launch request and pid history in
  `/home/Vinscen/decisions/artifacts/focused-harness/gemma4-gpu0/`.
- The three tools will change here. When you change one, note it in `UPSTREAM.txt`
  under a "diverged" line so the fork point stays legible.
