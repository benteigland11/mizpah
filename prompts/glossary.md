# Glossary

The words every seat in this loop uses, with one meaning each. A term below is used in that sense and no other, by the controller, the worker and the reviewer alike; where a tool's command is the term's verb, the command is named. The glossary names; the philosophy files explain the workflow.

## General

- **Requestor** — Entity that has authority to authorize briefs and all administrative actions. User is always a requestor.

## Terra

- **Brief** — the reference: What the task is commisoned for. Defines the I/O as well as restrictions. All edits approved by requestor.
- **Need** — A standard that shall be followed or utilized.
- **Deliverable** — An artifact the task must manufacture and present.
- **Non-goal** — Standards for things that should be ignored or not pursued.
- **Budget** — Points assigned to a brief that set caps on the allowable budget and provide insite into the "size" of the project from the requestors perspective.
- **Map** — Evidence map to provide confidence in the state of the project. The way to make a non verifiable project have confidence by collecting verifiable chunks and composing them. Every task includes 1 global map, while sub maps are allowed for more focused work and exploration.
- **Unknown** — A named exploratative driver that the work shall resolve. Used as vehicles to provide evidence for knowns in the map through verifiable work.
- **Quantity** — Typed value the unknown can be mapped to. Probes will provide these as evidence to the known. Types: number, boolean, label, formula, relation.
- **Probe** — Instrument that provides evidence towards an unknown's quantity to graduate it to a known by producing a reading.
- **Reading** — Values produced by probes that get accepted to a map through quantities.
- **Run** — Stamped execution of a probe which is the pointer used to provide evidence to graduate an unknown.
- **Known** — An unknown that has been provided with enough evidence from probes to be considered useful to prove the design attached with a confidence rating.
- **Ladder** — Command to run a probe multiple times to hit a threshold of confidence in the value. Used for probes that return variable results.
- **Adopt** — Method to pull knowns from a sub map to the global map.
- **Gate** — The passing grade: a mechanical check over the whole map. Green when it carries no debt — no open unknown, every known backed, nothing stale, methods agreeing; red names each debt. The worker turns it green on its task map; the controller routes on its red. `terra gate`.
- **Route** — The list of tasks the controller hands down to the worker with assigned buckets for effort expected. Each route is turned into a work order.
- **Bucket** — The effort of a task, priced in points: **low** (3) implement, path known; **medium** (8) validate, weigh a couple of options then conclude; **high** (21) explore several.
- **Sitrep** — The controller's one-call orientation: Brief, route, budget, map, gate, prior art, workspaces. `terra sitrep`.
- **Proposal** — The controller's request to change the Brief due to discoveries during the task.

## Playbook

- **Procedure** — a method in the playbook: a named checklist of steps a worker walks. `playbook search`, `load`, `create`, `add-step`, `edit-step`, `validate`.
- **Step** — one item of a procedure: a title and what to do. A step may link another procedure.
- **Walk** — a procedure opened for one task as a checklist file under `.playbook/open/`, flattened with everything it links (`playbook open <id> --for …`).
- **Plan** — the walk's plan file, the worker's placement of every step before any tick.
- **Tick** — marking one step of a walk `[x]` done with a note, or `[-]` skipped with a reason, on the command that did the step's work (`playbook tick`).
- **Reach** — how long a method really is through its links.
- **Gravity** — how many procedures link into one.
- **Improve** — changing the procedure that was walked so its next walk is better: a step added, edited or reordered. Allowed after green.

## Cartograph

- **Widget** — an instrument in the Cartograph library: general code with an API, tests, examples and a README, installed under `cg/<domain>-<name>-<language>/`. A widget takes the thing it acts on as an argument; it never carries the project's material. `cartograph search`, `install`, `create`, `validate`.
- **Check-in** — a widget entering the library. Done by the host after green, never by the worker.
- **Blueprint** — several widgets behind one API.
- **Glue** — the short project-specific script that calls widgets with this project's material. Glue is not a widget and does not transfer.

## Mizpah

- **Task** — The combination of environments, work orders, propsals, and daily briefs that perform the job handed down by the Brief performed by the controller, worker, and reviewer.
- **Work Order** — A route entry that includes the unknowns it resolves, bucket, dependencies that is handed to the worker and is the main unit of progress.
- **Library** — what persists across projects: the playbook (procedures) and the widget library (widgets). Nothing else transfers.
- **Instrument** — a probe or a widget: a thing that measures or acts. An instrument is never itself a finding.
- **Base** — a saved environment a gym mounts read-only (toolchain, venv, data). What is needed beyond it is installed in the project.
- **Harvest** — at green, the host's collection of what the worker minted while working: widgets validated and checked in, procedures created or improved merged to the store.
- **Controller** — owns brief, unknowns, route. Never uses the playbook; never sees the worker's transcript; never instructs the worker.
- **Worker** — owns procedures, widgets, probes, knowns, map writes. Works one work order in a sandbox with `bash`, `read`, `write`, `edit` and the tools' commands.
- **Reviewer** — the controller's look over the worker's shoulder at a completion claim: are the probes honest — does the reading come from the named source, and would a wrong artifact fail it. It never judges execution, order, or turns.
- **Correction** — the reviewer's one instruction to the worker: the delta — what the files show is missing and what to keep — never a restated requirement, a tool, a method or a source by name. Held until the reviewer looks again; withdrawn when the files no longer show it.
- **Guidance** — the correction as the worker sees it, dated with the turn it was issued and standing until the next look.
- **Claim** — the worker's `done` after `terra route complete` succeeded. The reviewer's look happens here.
- **Departure** — what the reviewer names: a probe or artifact that does not do what the brief entry or unknown says.
- **Turn** — one worker model call and the tool results it caused. Turns are cost, not score.
- **Window** — the worker's context. **Rollover** — the window reaching its threshold. **Handoff** — the memory the worker writes for the next window.
- **Session** — one worker's saved state for one work order: journal, windows, workspace. A paused session reopens where it was.
- **Workspace** — the worker's tree under `/work`: the project, its `.mizpah` state, its playbook walks, its `cg/`.
- **Sandbox** — where every command runs: the workspace bound in, the base read-only, no network beyond the allowed hosts, the toolchain and the engine invisible.
- **Green / red** — the gate's verdict on a work order. Only after green may the worker write or improve a procedure.
- **Estimate** — the bucket's turns; past it the worker is asked whether to continue, and decides.
- **Nudge** — a note from the requestor to a running worker, delivered once at the next turn boundary.
