# Design: one turn of the wheel

Controller owns Brief, Unknown, Route, Project Eval. Worker owns Procedure, Widget,
Probe+run, Known, Map writes. Gate is Terra's. The user holds the brief.

**The controller wakes** with a small context: the brief and a sitrep. It compares
reference to state and mints unknowns — each citing the need or deliverable it serves —
and adds route tasks, one per unknown, bucketed and ordered by dependency. A handful of
model calls; no tool investigation. It sleeps.

**A worker wakes on one task.** Its context: the task text, the unknown it resolves, the
tools. It does not see the brief. First act: `playbook search` for a method; `start`
returns one step. It follows steps one at a time — identify inputs, find or create the
widget the measurement needs (`cartograph search`, else `cg create → validate → checkin`),
write a probe that calls it, `terra probe validate` / `run`, graduate the unknown to a
known citing the run, complete the route task citing the run. It never holds the whole
procedure or the brief in context.

**Gate runs** mechanically. Red names the specific weakness ("radiator_area: confidence
low, n=1") and the route continues. Green means the method got the project through
everything the reference asked; only now may the worker write the procedure it followed
(or an improvement to it) back into the playbook. The playbook is therefore a library of
methods that have each closed a loop.

**Project Eval** (controller) reads gate + sitrep against the brief. Output is only two
things: new unknowns, and brief proposals with evidence. It never addresses the worker.
The user accepts or rejects proposals in the app; the brief bumps a version; the next
cycle's unknowns are computed against a sharper reference.

## Seams still open

- Terra may need scoping down for this use; it carries assumptions, cohorts,
  calculations, design baselines, scopes and suites that the loop does not route around.
- The controller's unknown-minting prompt is the hardest prompt in the system. The
  structural guard (must cite a brief id) is the floor, not the answer.
- Autonomy ramp for Project Eval: what evidence earns auto-accept, per proposal class.
- Engine ↔ app contract (HTTP vs stdio) and the sidecar packaging of the uv environment.
- Which harness pieces become the worker body as-is (`persistent_model_session`,
  sandbox, bounded dispatch) and which are rewritten against Terra/Playbook directly.
