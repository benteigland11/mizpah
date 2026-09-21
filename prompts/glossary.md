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

- **Procedure** — Unit stored in Playbook that holds the information for how things are carried out to the best of Mizpah's knowledge. A constant WIP.
- **Step** — One sequential item in a procedure that describes how to carry how the work or what procedure to use. Each gets checked off as the work has completed.
- **Plan** — File based on workers reading of the procedure. Used as the filter to select what is relevant for the current work at hand.
- **Tick** — Marking a step as done [x] or skipped [-] after the work has carried out.
- **Reach** — Total steps if the entire chain of procedures was carried out. Used as a feedback signal to break down work orders and provide task work estimates.
- **Gravity** — Measurement of procedural linking to help determine the LCD procedure. This is so we can identify best entry points.

## Cartograph

- **Widget** — Mini library in 1 language that gets stored, resused, and improved over time with work. Carry metadata, src, tests, and examples that all must pass through a langauge native validation gate. Stores general methods and not hardcoded project specific ones.
- **Check-in** — Widget passing validation gets checked in to the registry where it can be found, improved, and used again.
- **Blueprint** — Composition of multiple widgets into 1 library which also runs through similar validation gates.
- **Glue** — Code written outside of the widgets and blueprints that is project specific and calls the widgets and blueprints.
- **Custom Rules** — Addition validation gates that can be added by the client to meet their specific validation needs per language. Runs alongside the Cartograph validation engine.

## Mizpah

- **Task** — The combination of environments, work orders, propsals, and daily briefs that perform the job handed down by the Brief performed by the controller, worker, and reviewer.
- **Work Order** — A route entry that includes the unknowns it resolves, bucket, dependencies that is handed to the worker and is the main unit of progress.
- **Library** — Units of knowledge persisting across all projects. Includes widgets, blueprints, and procedures.
- **Instrument** — Items that get checked in to the library and can be reused and improved.
- **Repo** — the requestor's own project: a repository the team works in, where the deliverables are theirs.
- **Gym** — a sandboxed, pre-configured environment the team is dropped into to get better at something, apart from any repo. Its deliverables are practice; only what reaches the library transfers.
- **Harvest** — at green, the host's collection of what the worker minted while working: widgets validated and checked in, procedures created or improved merged to the store.
- **Controller** — owns brief, unknowns, route. Never uses the playbook; never sees the worker's transcript; never instructs the worker.
- **Worker** — owns procedures, widgets, probes, knowns, map writes. Works one work order in a sandbox with `bash`, `read`, `write`, `edit` and the tools' commands.
- **Reviewer** — the controller's look over the worker's shoulder at a completion claim: are the probes honest — does the reading come from the named source, and would a wrong artifact fail it. It never judges execution, order, or turns.
- **Correction** — the reviewer's one instruction to the worker: the delta — what the files show is missing and what to keep — never a restated requirement, a tool, a method or a source by name. Held until the reviewer looks again; withdrawn when the files no longer show it.
- **Claim** — the worker's `done` after `terra route complete` succeeded. The reviewer's look happens here.
- **Turn** — one worker model call and the tool results it caused. Turns are cost, not score.
- **Handoff** — the memory the worker writes for the next window when its context rolls over: what is established, what is unfinished, where to continue.
- **Workspace** — the worker's tree under `/work`: the project, its `.mizpah` state, its playbook walks, its `cg/`.
- **Sandbox** — where every command runs: the workspace bound in, the base read-only, no network beyond the allowed hosts, the toolchain and the engine invisible.
- **Estimate** — the bucket's turns; past it the worker is asked whether to continue, and decides.
