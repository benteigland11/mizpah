# Agent Control Theory

Research hub for the two conversations **Build decision-making tool** and **Simplify prompt token divergence**, reviewed 2026-09-06. The published piece explores LLM harnesses through a control-theory lens. The retained work includes real token captures, illustrative simulations and the earlier decision-system reasoning.

Start with [Lessons learned](review/LESSONS.md), [model assumptions](review/MODELS.md), or [reproduction and reusable assets](review/REPRODUCE.md). The [artifact manifest](review/artifact-manifest.json) records local provenance and hashes; [validation](review/refactor-validation.json) records the cleanup replay.

## Article assets

Separate PNGs are retained with their original titles and minimal chart styling. Notes and definitions belong in the supporting document.

| Asset | Local file | Existing Drive copy |
|---|---|---|
| Control-loop schematic | [PNG](agent-feedback-loop.png) · [editable source](render_agent_loop.py) | [Drive](https://drive.google.com/file/d/1iBvhgfGcf9cxuf3ja1RF_yk84OiNkOoa/view) |
| Gemma4 31B agent paths | [PNG](agentic-experiment-31b/agent-lanes.png) | [Drive](https://drive.google.com/file/d/19s59fSCYJEyBIGei5GwVARH0IZPeXNT7/view) |
| Gemma4 31B token similarity | [PNG](agentic-experiment-31b/order-vs-unordered.png) · [pair values](agentic-experiment-31b/order-vs-unordered.json) | [Drive](https://drive.google.com/file/d/1Hua21TuNwOq6TVkYhe3ueXejSot1Ut4o/view) |
| Normal sampling bell curve | [PNG](plots/normal-sampling-bell-curve.png) | [Drive](https://drive.google.com/file/d/1Q3xSoG4RxPGDkHfGfX4nq44HyZo-PvHp/view) |
| Normal feedback trajectories | [PNG](plots/normal-feedback-trajectories.png) | [Drive](https://drive.google.com/file/d/1CC-1fjtjRMBqK22uOF31nimvx6JgZbV6/view) |
| Equal goal/history density | [PNG](plots/equal-goal-history-distribution.png) | [Drive](https://drive.google.com/file/d/15i2o78Srnn2uBfNz-p3f1dHaj2pbKTbL/view) |
| Equal goal/history paths | [PNG](plots/equal-goal-history-trajectories.png) | [Drive](https://drive.google.com/file/d/1Cd0RZSPb0oBL9FM1d6sgbX0Saz-M7Jt8/view) |
| Drifting prompt paths | [PNG](plots/drifting-goal-history-trajectories.png) | [Drive](https://drive.google.com/file/d/1tTiRAGwmNHbOSzi3yFHihNbEnZJ2BDHn/view) |

[Drive folder](https://drive.google.com/drive/folders/1MDBvPm_SamCOA8iwuB9Vr1ko9TOd80_Z) · [Initial prompt and article background document](https://docs.google.com/document/d/1aiavbYgbZbLHydCdp5aU2P8_uJbf9cuOpq1Qi4uKIFE/edit) · [Local initial agent prompt](agentic-experiment-31b/task-prompt.txt).

These Drive references were recovered from the conversations. This retrospective is local; it does not automatically update remote copies or alter the already-published article.

## Measured evidence

| Experiment | Completed coverage | Interpretation |
|---|---|---|
| [Repeated editing loop](llm-experiment/README.md) | 6 seeds × 10 responses = 60 records | Real sampled responses to a related editing chain; no autonomous tools. Embedding distance is a text proxy, not task correctness. |
| [OpenCode 12B](agentic-experiment/README.md) | 6 runs; calls 9, 20, 18, 12, 20, 17; frozen tests pass 4/6 | Exact backend IDs and real tool feedback. Failures retained. |
| [OpenCode 31B comparison cohort](agentic-experiment-31b/README.md) | Capture runs 1–6; calls 6, 7, 7, 7, 9, 7; tests pass 6/6 | Same task/seed schedule, but different quantizations and model identity; not an isolated parameter-count experiment. |
| [OpenCode 31B article cohort](agentic-experiment-31b/order-vs-unordered.json) | Capture runs 7–12; calls 13, 5, 9, 8, 6, 7; tests pass 6/6 | Chart labels Runs 1–6. All 15 pairs over 5 common calls. Similarity drops overall, not monotonically. |

The raw-ID [turn-aligned trajectories](agentic-experiment-31b/token-trajectories-31b.png) remain available. ID magnitudes are arbitrary vocabulary labels. [Shared-token attribution](agentic-experiment/shared-token-distribution.json) examines whitespace and syntax rather than assuming they explain the overlap. Historical LCS and cumulative-prefix charts remain in their original experiment directories and are distinct from the article's Levenshtein chart.

These results establish variation in sampled sequences and agent paths under the recorded conditions. They do not identify a nonlinear gain, prove inevitable drift, or measure error amplification from one controlled token perturbation. The six successful article runs demonstrate why lexical divergence and goal failure should be evaluated separately.

## Compaction and noise

[Compaction-window sweep](compaction-window-sweep/README.md): controller off, 10,000 turns × 5 runs × 5 seeds, windows 25 to none. Every noise measure falls monotonically with shorter windows (6–10× overall); error against the reference does not fall. Noise and reference error are separate levers.

## Long-horizon preview

[10,000-call noisy-input baseline](long-horizon/README.md): ten toy response trajectories with preserved 160-call prefixes and saved draws for later controller comparisons.

## Reusable toy baseline

The [Toy LLM Feedback blueprint](../../cg/bp_toy_llm_feedback_python/README.md) preserves the final normal-to-bimodal goal/history model behind one API. Its default 10-run, 160-step replay matches the saved equal-mixture data exactly. Fixed and drifting goal scripts now use it; [validation](review/blueprint-validation.json) records unchanged PNGs and data.

## Models and sources

[Model catalog](review/MODELS.md) distinguishes the final goal/history examples from earlier saturated, categorical, dense and normal-feedback variants. All are illustrative assumptions. [Sampling-rank research](sampling-rank-research.md) records primary evidence and the limits of estimating how often a model selects its top token.

The schematic is `y_k = F(x_k, ξ_k)`: `k` counts model calls, `x_k` is the assembled input sequence, `y_k` is the generated sequence, and `ξ_k` is sampling randomness. `F` includes the model and decoding procedure. Input junctions mean context assembly, not numerical addition; the history arrow does not establish signed gain or stability. System/user prompts enter the input side.

For the original decision-system framing, see [DESIGN.md](../../DESIGN.md) and [SEMANTICS.md](../../SEMANTICS.md) in the workspace. Playbook stores methods, Cartograph stores implementations, and persistent questions keep collection and analysis directed toward the project objective.

## Rebuild

Use [the replay instructions](review/REPRODUCE.md) for experiments and simulations. To rebuild only the standalone schematic on another device, copy `render_agent_loop.py` and `requirements.txt`, then run:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python render_agent_loop.py
```

On Windows PowerShell activate with `.venv\Scripts\Activate.ps1`. Inspect the resulting PNG. The full analysis scripts additionally require the workspace's extracted widgets; they are not single-file portable packages.
