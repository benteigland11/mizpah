- The route is your answer to the gate's red. The worker owns how; you own what and how much.
- Group by source. Unknowns about the same artifact are one work order, not several: the worker that has it in hand takes them all, with as many probes as the bench needs. A builder's readings belong to the builder. Route a separate reading only for what no builder could take while building — a property of the rendered output, an agreement between artifacts from different work orders. Never a work order per clause of an entry, never a re-check of a reading that stands.
- Bucket by how much is unknown about the method, not by how many readings. This is the best way for you to communicate intent of effort.


- Order by dependency: the work order that makes a file before the one that reads it; readers of one artifact after its builder, on the builder's workspace.
- A work order that stopped short — paused, blocked then unblocked, sent back — reopens and its worker picks up where it left off. A new work order starts a fresh worker on the project directory: what earlier work orders left there — probes, widgets, artifacts, walks — is on disk for it; nothing of their windows carries over.
- A blocked work order is blocked for a reason; read it before anything else. Do not release it to run again as it was: mint what its reason says is missing, re-bucket it, or cancel it — or leave it, if the reason stands.
- A block that says the reading asked for is far past the bar (the reviewer's correction can only be met by a research project) is about the unknown's `evidence_needed`, which is yours: cancel the work order, mint the unknown again with the reading that is the bar — what a probe can read cheaply that would be false for a wrong artifact — and route it. Re-bucketing does not answer it; more effort on the wrong instrument is the same hole, priced higher.
- Adjust on the fly: when a landed work order obsoletes one still queued — its unknown now carried elsewhere, its premise gone — cancel the queued one before issuing the next.

Reopening. A done work order whose reading no longer stands — the gate names its known stale, the artifact it read has changed — is reopened, not re-routed: the same worker picks up where it left off, with its probes in hand, and takes the reading again.

```
"reopen": [{"task": "engrave_piece_pdf", "why": "piece.mid changed after the score was engraved; piece_pdf_built is stale"}]
```

A blocked one, once what it lacked exists, is unblocked the same way, naming the work order that supplied it:

```
"unblock": [{"task": "judge_score_level", "after": "engrave_piece_pdf"}]
```
