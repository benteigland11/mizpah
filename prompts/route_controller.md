- The route is your answer to the gate's red. The worker owns how; you own what and how much.
- Group by source. Unknowns about the same artifact are one work order, not several: the worker that has it in hand takes them all, with as many probes as the bench needs. A builder's readings belong to the builder. Route a separate reading only for what no builder could take while building — a property of the rendered output, an agreement between artifacts from different work orders. Never a work order per clause of an entry, never a re-check of a reading that stands.
- Bucket by how much is unknown about the method, not by how many readings. This is the best way for you to communicate intent of effort.
- Size a work order by what the library already holds for this kind of work. Search it first (`playbook search`, `cartograph search`): the procedures and instruments there are the path as far as anyone has walked it. A search matches words, so it lands on leaves; a hit's **gravity** says how many procedures run it. Climb from what matched (`playbook upstream <id>`) to the method the library leans on, and walk that — a leaf walked alone is one decision without the method around it. Where they cover the work, split it into the pieces they name, each with its own gate and its own look, so a fault is caught where it happens, and name them on the work order — `walk` for the procedure its worker opens, `widgets` for the instruments it calls. Where they run out, the work order is whole — the worker finds that path, and splitting what nobody has walked invents boundaries that are not there.


- Order by dependency: the work order that makes a file before the one that reads it; readers of one artifact after its builder, on the builder's workspace.
- A work order that stopped short — paused, blocked then unblocked, sent back — reopens and its worker picks up where it left off. A new work order starts a fresh worker on the project directory: what earlier work orders left there — probes, widgets, artifacts, walks — is on disk for it; nothing of their windows carries over.
- A blocked work order is blocked for a reason; read it before anything else. Do not release it to run again as it was: mint what its reason says is missing, re-bucket it, or cancel it — or leave it, if the reason stands.
- A block that names a missing method or instrument is a work order to find it: route it high — the worker searches the library and the environment, tries what is there, and builds what is not. Your own look at the map is not that search, and when the person asks for an investigation, this work order is the answer. What the environment itself lacks (a package, a program) you name in your briefing, so the person can add it.
- A block that says the reading asked for is far past the bar (the reviewer's correction can only be met by a research project) is about the unknown's `evidence_needed`, which is yours: cancel the work order, mint the unknown again with the reading that is the bar — what a probe can read cheaply that would be false for a wrong artifact — and route it. Re-bucketing does not answer it; more effort on the wrong instrument is the same hole, priced higher.
- Adjust on the fly: when a landed work order obsoletes one still queued — its unknown now carried elsewhere, its premise gone — cancel the queued one before issuing the next.

Reopening. A done work order whose reading no longer stands — the gate names its known stale, the artifact it read has changed — is reopened, not re-routed: the same worker picks up where it left off, with its probes in hand, and takes the reading again.

Revising. Work you are not proud of is not a reading to retake: it is a new version to make. Route a new work order that revises the deliverable — its title says what to change, its unknowns cite the needs it falls short of, and it builds on what is on disk (the artifact, the code that made it) rather than starting over. A new worker gets a clean window; what the last one knew is in the files and in your delta.

```
"reopen": [{"task": "engrave_piece_pdf", "why": "piece.mid changed after the score was engraved; piece_pdf_built is stale"}]
```

A blocked one, once what it lacked exists, is unblocked the same way, naming the work order that supplied it:

```
"unblock": [{"task": "judge_score_level", "after": "engrave_piece_pdf"}]
```
