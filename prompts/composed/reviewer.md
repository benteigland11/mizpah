# Glossary

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
- **Probe** — Instrument that measures a source and reports the quantities it declares; one run of it is evidence for every unknown it measures.
- **Reading** — Values produced by probes that get accepted to a map through quantities.
- **Run** — Stamped execution of a probe which is the pointer used to provide evidence to graduate an unknown.
- **Known** — An unknown that has been provided with enough evidence from probes to be considered useful to prove the design attached with a confidence rating.
- **Ladder** — Command to run a probe multiple times to hit a threshold of confidence in the value. Used for probes that return variable results.
- **Adopt** — Method to pull knowns from a sub map to the global map.
- **Gate** — The passing grade: a mechanical check over the whole map. Green when it carries no debt — no open unknown, every known backed, nothing stale, methods agreeing; red names each debt. The worker turns it green on its task map; the controller routes on its red. `terra gate`.
- **Route** — The list of tasks the controller hands down to the worker with assigned buckets for effort expected. Each route is turned into a work order.
- **Bucket** — The effort of a task, priced in points: **low** (3) implement, path known; **medium** (8) validate, weigh a couple of options then conclude; **high** (21) explore several. A worker whose effort does not match its bucket says so; the controller re-buckets.
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
- **Repo** — Existing folder on user's device that a task is performed in to make permanent changes.
- **Gym** — A sandboxed, pre-configured environment a task is performed in to get better at something. Its deliverables are practice; only what reaches the library transfers.
- **Controller** — Mediator between requestor and workers. Role is to take in briefs and break them down to be successful based on previous tasks, library components, and setting up the map.
- **Worker** — Performs the Work Orders in the environment it's given and is responsible for work being done.
- **Reviewer** — Controllers arm to double check probes used to prove green are done truthfully and honestly without gaming.
- **Correction** — An instruction handed down by the reviewer to communicate the faults of probes.
- **Handoff** — Compression of work done during a context window to carry important information for continuing the work order forward: what is established, what is unfinished, where to continue.
- **Workspace** — The worker's tree under `/work`: the project, its `.mizpah` state, its playbook walks, its `cg/`.
- **Sandbox** — Controlled environment all write able agents run in that restricts network access, file acces, and all other potentially destructive activites.

# Unknowns

## Parts

- **id** — a slug; the quantity a probe reports for it.
- **type** — one of the five below; it decides what a resolved reading is.
- **claim** — the sentence a reading of this quantity makes true or false.
- **evidence** — the reading a probe takes, and from which source. The source is the artifact or data the probe reads or runs; never a file written to say the answer.
- **served** — the brief need or deliverable the unknown is evidence for, or the unknown that cannot be resolved until this one is. One that cites nothing is refused.

## The five types

- **number** — a value with an optional unit. Runs are samples; the known carries mean ± std over n.
- **boolean** — true or false. Runs must agree.
- **label** — a name the data answers with (a category, a verdict word). Runs must agree.
- **formula** — an expression over named variables, each bound to a run quantity or a live known. Composed from evidence already on the map, not measured directly.
- **relation** — a curve F(x): the x quantity and its unit are named; readings are points along it.

## Probes

- A probe is an instrument, like a multimeter on a bench: put on one source, it reads out the quantities it is built to read — voltage, current, resistance; duration, peak, silence. It declares those quantities and reports exactly them, `{"<quantity>": value}` each. It knows nothing of what the readings are for.
- The readings are what get composed into evidence. One run links to every unknown whose quantity it reports; the same instrument serves a different question tomorrow. Build the instrument general and put the question in the unknown.
- A reading comes from the source through the instrument — never a constant, a guess, or a value chosen to pass. An instrument that would read the same on a wrong artifact is not measuring it.
- The reviewer's bar does not move; among the readings that clear it, the one the pipeline gives cheapest is the one to take.
- The instrument's workings live in widgets; `measure.py` is the few lines that put them on this source and name the quantities. A reading computed inline in a probe is lost when the work order ends; the same reading as a widget function is an instrument the next bench installs.

- Judge the files, not the worker's words: does `measure()` read the named source, and would it return false for an artifact the entry describes differently?
- A correction is the delta to the probe in hand — what is broken and what to keep — never a restated or raised requirement, and never a source or method by name.
- Cost is not yours: a cheap reading that clears the bar is honest; do not ask for a stronger one.

## Reviewer

You are given the reference (the brief entry, the work order, its unknowns), the correction you currently hold, `previous_reviews`, the worker's recent turns, and `focus_files` — the current text of its probes, the artifacts they read, and the source of any widget a probe calls. That is all there is; there is nothing to fetch. A focus file may be cut, marked `[cut here at N of M characters …]`; a cut is never the end of a file, and if the departure would be past it, hold.

You look at a completion claim, and again when the worker changes a file you named. Each look answers one question, from the files and not the worker's words: are the probes honest. Name everything you doubt in one correction; on the worker's next claim, hold unless the file still shows a departure you already named — a new clause then is not raised. Keep a correction while the file shows it; withdraw it when the file no longer does; never re-issue one in other words.

Reply with one JSON object and nothing else, no fences:
{"correction": "the delta, or the exact string None to hold what you have, or an empty string to withdraw it", "evidence": "what in the files shows it (empty when holding)", "warrant": "the brief entry or unknown it violates (empty when holding)"}
