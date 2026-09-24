# Contributing

Thanks for looking under the hood. This page is the map of the repository and the rules that keep it
coherent. The words used here (brief, unknown, probe, known, gate, work order, procedure, widget)
each mean one thing; they are defined in [`prompts/glossary.md`](prompts/glossary.md).

## Setup

```bash
uv sync --python 3.12 --managed-python
```

One uv workspace, one interpreter; everything runs from `.venv`. The app is in `app/` and needs the
Flutter SDK and the system packages listed in the [README](README.md).

## Layout

| Path | What it is |
|---|---|
| `app/` | The Flutter desktop app. It starts the engine from this checkout's `.venv`, or talks to one on another machine over its RPC desk. |
| `engine/mizpah/` | The loop: controller, workers, reviewer, Deputy, the gate, provider sign-in. `config.openai.json` is the one engine config. |
| `engine/harness/` | Bounded agent sessions, the wire view, the sandboxed shell, model clients. `tools/` has the campaign runner and the harness config. |
| `engine/terra/` | Terra (fork): briefs, the evidence map (unknowns, probes, runs, knowns), the route, the gate. |
| `engine/cartograph/` | Cartograph (fork): the widget library, validation and check-in. |
| `engine/playbook/` | Playbook (fork): procedures, walked one step at a time. |
| `engine/casebook/` | Casebook: problems seen before and what fixed them. |
| `prompts/` | What each seat is told, as small composed pieces. See its README. |
| `notices/` | The messages the app sends its user, one file each. |
| `design/` | How a mechanism is meant to work, for people designing the loop. Never fed to a seat. |
| `docs/` | Design overview and the research record behind the rules. |

## Rules that keep it coherent

- **The forks stay legible.** Terra, Cartograph and Playbook are forks and still work standalone.
  When you change one, add a line under "diverged" in [`UPSTREAM.txt`](UPSTREAM.txt) saying what and
  why.
- **`cg` is a namespace package** merged from several packages. Never add `cg/__init__.py`.
- **Reusable code is a widget.** A piece of logic another project would want lives under a `cg/`
  directory as a Cartograph widget (source, tests, examples, `widget.json`) and passes
  `cartograph validate`. Glue that only makes sense here stays in the package.
- **One engine config.** `engine/mizpah/config.openai.json` and the harness config it names hold
  policy and defaults only. What a person chooses on their machine goes to
  `~/.config/mizpah/config.json`; a project's own choices go to its `.mizpah/config.json`. Never
  commit a home path, a local server or a model pick.
- **Prompts are composed, never pasted.** One file, one thing; the file name says which seat gets it.
- **Teach a method, never a number.** Seat prompts state general rules. A prompt that names the answer
  to a benchmark spoils the benchmark.
- **The library is the loop's.** Procedures and widgets the agents wrote are not hand-edited between
  runs. If one is wrong, that is a finding about the harness; fix the harness and let the loop redo it.
- **No em dashes** in public text.

## Tests

Each package tests from its own directory:

```bash
(cd engine/terra      && ../../.venv/bin/python -m pytest -q)
(cd engine/cartograph && ../../.venv/bin/python -m pytest -q)
(cd engine/playbook   && ../../.venv/bin/python -m pytest -q)
(cd engine/casebook   && ../../.venv/bin/python -m pytest -q)
(cd engine/mizpah     && ../../.venv/bin/python -m pytest -q)
```

Widgets test one at a time, with the widget on the path:

```bash
cd engine/harness
PYTHONPATH=$PWD:$PWD/cg/<widget> ../../.venv/bin/python -m pytest -q cg/<widget>/tests
```

The app:

```bash
cd app && flutter analyze && flutter test
```

Tests that need the sandbox (bubblewrap and a user systemd) skip without them. Tests never read or
write your `~/.config/mizpah`; each gets an empty one of its own.

## Pull requests

Keep a change to one idea, with its tests. Say in the description what changed and why, in the words
of the glossary. Commit messages here say what the change makes true and the evidence for it; the
history is part of the design record.

By contributing you agree your work is licensed under the Apache License 2.0.
