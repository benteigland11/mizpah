# casebook

Cases: a problem that cost a worker turns, filed under the symptom as it presented, with what it turned out to be,
what fixed it, and every episode it happened in. A stuck worker searches with what it sees; the case is found by
that. One case per diagnosis — a new symptom of a known cause is another instance of it.

```
casebook search <what you see>        # the error, the red reading, the surprise
casebook show <id>
casebook add --problem … --diagnosis … --fix … --where … --symptom … [--change …] [--into <id> | --new]
casebook add --file case.json
casebook list | validate [<id>]
```

The general fields (problem, diagnosis) refuse file names, paths and snake_case ids: those belong to one episode and
go in its instance. The store is `$XDG_DATA_HOME/casebook/cases/` (the platform data dir elsewhere).

Not a fork: written in Mizpah (2026-09-23) to stand beside Playbook while procedures are sidelined. The miner that
finds episodes in a worker's trace and the writer that turns them into cases are in `engine/mizpah`
(`mizpah.episodes`, `mizpah.casewriter`). Widgets: infra-app-paths, infra-atomic-file-write (Cartograph), and
universal-bm25-ngram copied from engine/playbook/cg (not in the widget library).
