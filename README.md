# Mizpah

A self-improving engineering loop. Three tools, forked and coupled by one harness:

- **Terra** (`engine/terra`) — the reference (brief) and the working set (map: unknowns,
  probes, runs, knowns), the route, the gate, the sitrep.
- **Cartograph** (`engine/cartograph`) — capabilities: widgets and blueprints that probes
  call.
- **Playbook** (`engine/playbook`) — methods: procedures a worker follows one step at a
  time.
- **Mizpah engine** (`engine/mizpah`) — the loop: a controller that reads brief against
  map and mints unknowns and route tasks; workers that take one task, find a procedure,
  build or reuse a widget, run a probe, graduate a known. Gate green is the signal that a
  method may be written back to the playbook.
- **App** (`app/`) — Flutter desktop; launches the engine as a sidecar and is where the
  user holds the brief (proposal queue, mode dial).

The tools are forked, not vendored: they will change here. Upstream commits are recorded
in `UPSTREAM.txt`. MCP servers, plugin manifests, docs and demos were left behind; the
harness couples the tools directly.

## Develop

    uv sync --python 3.12 --managed-python
    .venv/bin/terra --help; .venv/bin/cartograph --help; .venv/bin/playbook --help
    (cd engine/terra && ../../.venv/bin/python -m pytest -q)
    (cd engine/cartograph && ../../.venv/bin/python -m pytest -q)
    (cd engine/playbook && ../../.venv/bin/python -m pytest -q)

One `uv` workspace, one managed interpreter; the same environment is what the app will
ship as its sidecar.
