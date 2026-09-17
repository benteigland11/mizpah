# Lineage

Mizpah grew out of the research workspace at `/home/Vinscen/decisions` (2026-09). These
files are copies frozen at the fork; the originals and the full campaign data (journals,
fixtures, snapshots) stay there.

| Here | Origin | What |
|---|---|---|
| `CONTROL-DESIGN.md`, `CONTROLLER-PHILOSOPHY.md`, `prompt-control-theory.md`, `README.md` | `artifacts/agent-control-theory/` | The control model: `y_k = F(x_k, ξ_k)`, inner history feedback, outer reference correction, integral input correction, cadence/history choice, relay controller endpoint |
| `compaction-study-design.md`, `compaction-window-sweep/` | `artifacts/agent-control-theory/` | "More compaction → less long-term noise", predicted from the model and then measured: 5 runs × thousands of turns, controller off, window swept |
| `bounded-context-working-set.md`, `small-windows-small-actions.md` | `artifacts/agent-control-theory/`, Claude memory | Why 60–90K windows force bounded tool actions and how the harness enforces it |
| `focused-harness-plan.md` | `artifacts/agent-control-theory/` | The plan the harness was built to |
| `campaigns/v6..v10-REPORT.md` | `artifacts/focused-harness/gemma4-pilot-v*-20260917/REPORT.md` | The development log: v4 3/6 → v9 3/6 at ~10× lower cost; v10 the closing state and the "corrections state the delta" lesson |
| `../../engine/harness/tools/` | `tools/focused-harness/` | Runner, qualifier, controller policy text, configs |
| `../../engine/harness/cg/` | `cg/` | The eight widgets the harness blueprint depends on |

Campaign data not copied: `artifacts/focused-harness/gemma4-pilot-*/attempts/*/session/`
(journals, event logs, workspace snapshots), `plan.json`, `fixture-manifest.json`,
`source-snapshot.tar.gz`. Read them there when a report is not enough.

Design canvas: https://claude.ai/artifact/47xMRDgkAUvhp5siPbw6jq (Flywheel Studio,
`design/main`).
