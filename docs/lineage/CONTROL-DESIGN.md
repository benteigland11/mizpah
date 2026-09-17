# Goal-directed feedback for an LLM harness

Working design note · 6 September 2026

**Current signal design:** [One reference branches into a clean controller reference and a drifting user prompt](prompt-controller-reference/prompt-controller.png). The controller also receives prior output and supplies a corrected prompt. This makes the source of the reference explicit; the earlier architectural resets below remain historical.

**Previous design connection:** [Output feedback now enters the next-prompt step through an undefined block](prompt-feedback-loop/prompt-feedback-loop.png). Conversation-history feedback remains separate. The block mechanism is deliberately unresolved; no original-goal signal, integral controller or output filter has been reinstated.

**Current architecture reset:** We have returned to the [baseline input/history loop](baseline-loop/llm-loop.png), removing the independent goal signal, error junction and controller. The controller design and results below are retained as exploratory history, not the current architecture. Correction of the fully assembled input remains to be designed.

## What we are trying to control

Keep an agent oriented toward its intended outcome over a long task while preserving useful variation in its responses and approach. Different wording, token sequences or action paths are not inherently errors. A productive detour is not necessarily drift.

Our toy model is a mathematical approximation of the LLM loop. The explanatory plant remains **Transformer LLM**, with

\[
y_k = F(x_k,\xi_k).
\]

The harness assembles context, preserves the goal, assesses progress and supplies corrective input. The complete response operation includes stochastic sampling. Feedback, nonlinearity, randomness and instability are distinct properties; merely returning output to context does not prove amplifying positive gain.

## The control loop

![LLM harness with input goal correction](input-correction-only/diagram/input-correction-loop.png)

| Signal | Meaning |
|---|---|
| Original goal, \(r\) | Preserved intended outcome and constraints; zero in the scalar approximation. |
| Input context, \(x_k\) | System instructions, user prompt, tool information, history and corrective guidance assembled by the harness. |
| Sampling randomness, \(\xi_k\) | Variation in response selection. |
| Response, \(y_k\) | Generated output; delivered without smoothing in the current design. |
| Goal error, \(e_k\) | Assessed deviation from the intended outcome. In the toy, \(r-y_k\); in a real harness, derived from work and evidence. |
| Correction, \(c_k\) | Guidance that changes the next input in response to goal error. |
| History delay, \(z^{-1}\) | One call: a completed response becomes context for a subsequent invocation. |

System instructions and tools form one harness input. The user prompt is a separate input, labeled p_k, that can change between calls. The combined instructions/tools input enters the first context junction; the user prompt enters the second, where conversation history also returns.

The inner feedback path returns conversation history to context assembly. The outer path assesses error against the original goal and adjusts subsequent input. The assembly junction is not numerical addition of token IDs. Likewise, the diagram's error subtraction stands for goal assessment in the real harness, not subtracting text from a number.

The goal supplies the reference for evaluation. **It does not itself detect drift.** Producing a useful error signal and deciding when it warrants intervention are separate responsibilities.

## Input failures: an initial miss and a wandering direction

| Failure | Interpretation | Consequence |
|---|---|---|
| Wrong preserved goal | Reference-setting error: the stated destination differs from the intended one. | Excellent control can hold the agent to the wrong outcome. The reference needs correction. |
| Ambiguous goal or prompts | Interpretations change over time, moving the effective direction supplied to the agent. | Can be approximated by larger or more frequent prompt-drift steps. This is a hypothesis, not a calibrated mapping. |
| Poor starting state | The work begins far from a correctly specified goal. | An initial-state error; distinct from choosing the wrong destination. |
| Noisy or biased assessment | Evaluation varies unpredictably or consistently misjudges progress. | Can produce unnecessary correction or consistently wrong steering, even with a good goal. |

A persistent offset and a rate of change are different. Drift need not have a constant slope: it can wander in either direction. A prompt offset relative to a correct preserved goal can be resisted; an incorrectly preserved goal cannot be diagnosed using that same reference alone. Legitimate goal changes must also be distinguished from accidental drift; how to authorize and update the reference is unresolved.

## What the approximation currently does

Each call samples from an equal mixture of a normal centered on the current corrected prompt and a normal centered on the previous response, both with standard deviation 0.25. Coincident centers give one normal; separated centers can give a bimodal distribution. This is an intuitive approximation, not a fitted distribution over token IDs.

Prompt drift accumulates equiprobable ±0.1 steps after randomized waits of 1–3 calls. It is not bounded to ±0.1. The added sine jitter was removed: the current experiment separates prompt drift from output sampling variation. Ten runs share one drift path and use saved paired component choices and Gaussian draws over 10,000 calls. This is a test horizon, not a conversion from calls into days.

Using the diagram's indexing, the current input correction is:

\[
e_k=r-y_k,\qquad c_{k+1}=c_k+0.05e_k,\qquad u_k=p_k+c_k.
\]

Here \(p_k\) is the drifting prompt coordinate and \(u_k\) its corrected value inside the approximation. Raw output becomes history directly. The controller updates after each response and its new correction acts on the next call. Zero error holds the accumulated correction; it does not reset it.

This is **integral input correction**, not the earlier lowpass prompt filter. It has no threshold, waiting period or explicit drift detector. Opposing errors can cancel, while persistent errors build correction—but random errors contribute too. The controller changed after all 100,000 sampled responses, including a diagnostic replay with prompt drift removed. It started correcting before the first prompt-drift event.

## What the comparisons showed

Mean per-run metrics on the same saved 10,000-call input and random draws:

| Intervention | RMS error from original zero | RMS successive response change |
|---|---:|---:|
| Unfiltered | 3.671 | 0.362 |
| Lowpass input only | 3.667 | 0.355 |
| Lowpass output and recirculated history | 3.645 | 0.031 |
| Integral input correction + output smoothing | 0.242 | 0.031 |
| **Integral input correction only** | **0.440** | **0.381** |

The input lowpass smoothed prompt changes but passed accumulated drift. Output smoothing reduced delivered volatility without restoring the original goal. Integral input correction alone reduced goal error by approximately 88% while retaining unsmoothed, slightly increased step-to-step variation. **That last case is the current design direction.** Roughness is not itself a measure of failure or pure noise.

Separately, six Gemma4 31B OpenCode article runs took different token/action paths and all passed the frozen tests. That supports preserving useful variation. It does not validate this controller on an actual LLM. The scalar approximation assumes accurate error measurement and sufficient correction authority; those are precisely the practical questions still open.

## Translating the error signal into an LLM loop

Preserve the outcome and constraints, assess current work using artifacts, tests and tool results, then express an observed gap as corrective guidance. For example: “The endpoint changes violate the compatibility requirement. Preserve the public API and focus on the login failure.” The agent retains freedom to choose a valid solution path.

A goal assessment need not be a scalar. We have not established a reliable evaluator, a noise-versus-drift decision rule, a correction cadence, or a mapping from numeric integral gain to prompt strength. Filtering or accumulating evidence about goal deviation could support selective input correction without filtering generated output; this remains a design question. Do not automatically treat token divergence or a change of approach as goal error.

The next design question is how to recognize persistent, meaningful departure from the goal while allowing ordinary output volatility. The existing controller demonstrates continuous error correction, not that distinction.

## Evidence and editable assets

- [Compaction-window sweep, controller off](compaction-window-sweep/README.md)
- [Filter comparison and exact metrics](filter-comparison/README.md)
- [Goal correction with output smoothing](goal-correction/README.md)
- [Current input-only run](input-correction-only/README.md)
- [Correction timing and zero-drift diagnostic](correction-timing/README.md)
- [OpenCode token and action-path evidence](agentic-experiment-31b/README.md)
- [Diagram SVG](input-correction-only/diagram/input-correction-loop.svg) · [drawing source](input-correction-only/diagram/render_diagram.py)

Reusable model: `bp-toy-llm-feedback-python@0.1.0`. Comparison/controller replay: `bp-feedback-filter-comparison-python@0.2.0`. Original data and the current diagram are unchanged by this note.
