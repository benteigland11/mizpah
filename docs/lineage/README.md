# The research record

Mizpah's rules were not designed up front; each one came out of an experiment. This folder is the
record: the control model the harness is built on, the study that set its context-window size, and
the reports from the ten agent campaigns that shaped it. [LINEAGE.md](LINEAGE.md) says where each
file came from.

- [CONTROL-DESIGN.md](CONTROL-DESIGN.md), [CONTROLLER-PHILOSOPHY.md](CONTROLLER-PHILOSOPHY.md),
  [prompt-control-theory.md](prompt-control-theory.md): the control model. A harness is a feedback
  loop; the brief is the reference; the controller corrects against the reference, not against
  execution mistakes.
- [compaction-study-design.md](compaction-study-design.md) and
  [compaction-window-sweep/](compaction-window-sweep/README.md): more compaction gives less
  long-term noise, predicted by the model and then measured.
- [bounded-context-working-set.md](bounded-context-working-set.md),
  [small-windows-small-actions.md](small-windows-small-actions.md): why small windows force small
  actions, and how the harness enforces it.
- [focused-harness-plan.md](focused-harness-plan.md): the plan the harness was built to.
- [campaigns/](campaigns/): the development log, v6 to v10.

A readable version of this story, rule by rule, is planned for mizpah.ai.
