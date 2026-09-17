# Compaction study: focused turns and persistent evidence

Design discussion, 2026-09-15. This specifies the next study; it is not an implemented change or a measured result.

The [focused harness plan](focused-harness-plan.md) maps the first implementation slice to existing reusable components and defines the end-to-end qualification milestone.

The subsequent design commits to 60–90K context windows and specifies [persistent working sets](bounded-context-working-set.md) to preserve understanding and refresh affected evidence across resets. This is a requirement for the proposed harness, not a change to the existing experiment configuration.

The user reports lower total output noise with more frequent compaction in the scalar model, with the controller disabled. This is now frozen as the [compaction-window sweep](compaction-window-sweep/README.md): noise falls monotonically with shorter windows while error against the reference does not. The proposed real-agent hypothesis is that earlier compaction, with an unchanged reference preserved, can reduce total cost while retaining or improving long-horizon task performance.

## Harness behavior

Bias the worker toward many small, purposeful turns. Each turn should resolve a specific uncertainty, make a coherent change, or verify a result. Related deterministic filtering and computation can happen inside one tool call. Small turns must still accomplish useful work; neither arbitrary fragmentation nor hard truncation of an unfinished model response establishes this behavior.

Large evidence belongs in persistent files. The active conversation should contain the question being investigated, a bounded observation, its source, and the next decision.

- Capture tool stdout and stderr automatically before constructing the model-visible result. Stream large output to a persistent artifact rather than relying on the agent to anticipate every large result.
- Return a bounded preview with the artifact path or handle, captured size, command status, and an explicit indication of whether capture is complete. A preview is an excerpt, not the whole result. Do not return a usable-looking handle before its artifact is durably available.
- Give the worker bounded search, line or byte ranges, and structured field selection. Large single-line JSON needs field or byte selection, not only line pagination. Apply the same output policy to these retrieval operations.
- Keep success, failure, timeout, and capture-limit information visible independently of the preview. Storage exhaustion must produce an explicit incomplete-capture result; preserving a prefix is not preserving the full output.
- Encourage the worker to ask a specific question of the artifact, inspect matching evidence, and act on it. Repeatedly reopening the whole file should not be the default recovery strategy.
- Preserve the unchanged reference, verified findings, unresolved questions, and useful artifact locations across compaction. The raw evidence remains retrievable without being automatically copied into the next context.

Illustrative flow: a test command produces a large log; the worker receives its status and a log handle; it searches for the failure, reads the relevant surrounding lines, makes one coherent repair, and verifies it. Deterministic selection should preserve provenance; an extra LLM summary is not required for every large result.

This policy reduces how much evidence enters and is repeatedly replayed in context. Text the agent itself generates still costs output tokens even when it is destined for a file. Record that cost separately from tool-produced output.

## Existing implementation and gaps

The current [shell worker](../../cg/infra_sandboxed_shell_execution_python/src/sandbox_worker.py) already returns bounded previews and saves captured stdout/stderr under `.tool-output`. The current exam configuration allows 2,048 inline bytes per stream and 1 MiB combined capture. Capture reaching the larger limit stops the command; saved output is then incomplete. The implementation buffers capture and writes the files at the end, so it is not yet the proposed streaming spill mechanism.

The [persistent session](../../cg/backend_persistent_model_session_python/src/persistent_model_session.py) rebuilds a context from unchanged base messages, a handoff, and a bounded recent-result tail. The existing [exam configuration](../controller-exam-2026-09-15/exam-resilient/pair-03-settings.json) permits one tool call per response, but that does not establish short, purposeful model turns or effective targeted retrieval.

These observations are source inspections. No runtime behavior or qualification result is claimed for the proposed additions. No widget code, running exam configuration, or validation rule was changed in this design discussion.

## Qualification before the compaction comparison

1. Produce oversized stdout, oversized stderr, and large single-line structured output. Verify artifact bytes, bounded previews, accurate status, and explicit incomplete capture at the storage limit.
2. Put decisive evidence outside the preview. Verify that the actual worker retrieves it through a targeted query and uses it correctly. File existence alone does not pass this gate.
3. Compact, then require another targeted lookup of the same artifact. Verify reference preservation, artifact identity, and correct continuation.
4. Measure useful progress per turn, model output length, tool-output tokens shown versus retained, repeated broad reads, retrieval calls, and total inference cost. Small visible messages can still hide excessive generated reasoning or repeated work.

## Experimental controls

Qualify this harness policy first and use it in every compaction arm. Hold the model, fixed reference, tools, persistent workspace, summary prompt and budget, and self-paced completion rule constant. Assign compaction thresholds before each run; record actual compaction counts and context sizes as outcomes. Each rebuilt context must leave room for useful new work.

Use matched tasks and repeated runs. Assess final correctness independently and include all worker, handoff, retrieval, and recovery work in cost accounting. Separate maintained quality at lower cost from improved quality. No aggregate stopping limit or new run schedule is introduced by this note.

## Research grounding

[Anthropic's tool-design guidance](https://www.anthropic.com/engineering/writing-tools-for-agents) recommends filtering, range selection, pagination, and useful truncation feedback for large results. That supports the interface design, not a claim that the worker will use it correctly.

[Agents Don't Paginate](https://arxiv.org/abs/2608.26130) reports no second-chunk requests in its observed middleware corpus. This is a reason to test retrieval behavior directly, not a universal claim about agents or proof that file-backed access fails.

[SelfCompact](https://arxiv.org/abs/2606.23525) reports accuracy and cost benefits from adaptive compaction. Its adaptive timing does not establish a monotonic benefit from increasing fixed compaction frequency.
