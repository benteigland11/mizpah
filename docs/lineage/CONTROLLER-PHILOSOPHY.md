# Controller philosophy

The prompt controller provides reference-based feedback control of the agent's direction. Its objective is to keep the work heading toward an outcome that satisfies the reference, while allowing the agent to choose its path.

![Control loop: clean reference and proposed prompt enter the Prompt controller; an undetermined observation block supplies output feedback; corrected input and accumulated history feed the Transformer LLM.](prompt-controller-reference/prompt-controller.png)

| Control term | Meaning in this loop |
|---|---|
| Plant | The Transformer LLM, which produces output from assembled context and sampling randomness. |
| Reference, r | The externally specified intended outcome and constraints. |
| Proposed input, p_k | The prompt presented for conditioning. |
| Observation block | Supplies feedback about the work. Its internal operation is undetermined. |
| Controlled property | The direction of the work relative to satisfying the reference. |
| Error | An assessed meaningful mismatch between that direction and the reference. |
| Manipulated input, u_k | The corrected prompt supplied to context assembly. |
| History, H_k | Accumulated output returned to context assembly through a separate feedback path. |

The **Prompt controller** uses the reference, proposed input and available observations to assess the direction of the work. Its governing question is:

> If the agent continues along this path, is it heading toward a result that satisfies the reference?
