# A behavioral model for prompt control

Discussion hypothesis, not an accepted implementation or a validated gain map.
The minimal system prompt remains unchanged.

Model the worker as inferring an active objective, a current situation, and a
preferred next action from its context. These are useful behavioral abstractions,
not claims about explicit internal registers. A correction can clarify a
reference requirement, repair a mistaken assessment of progress, or change the
priority of a next action. Its strength is the resulting behavioral change.

For the filtered-export fixture, three different interventions are:

- Requirement: “The export must contain exactly the visible filtered rows.”
- Current state: “The current export still includes rows hidden by the filter.”
- Next action: “Correct row selection before continuing cosmetic changes.”

These target different mechanisms. They are not three ordered gain settings.

P concerns immediate redirection given current reference-relative evidence.
I concerns correction that must persist across observations. Previously issued
guidance is evidence about earlier interventions, not automatically an integral
of error. Repeating a record is not another independent measurement. The
selected history length is a concrete input parameter, but is not by itself Ki.

For a prompt-only controller, define an observable response before claiming a
gain: for example, which next substantive action the worker takes, and whether
it resolves the discrepancy within a fixed horizon while preserving already met
requirements. Hold the reference, proposed prompt, history, model and decoding
configuration fixed when comparing interventions. Include aligned cases and
cases with resolved defects to detect unnecessary correction. Measure both the
controller's rewrites and the worker's subsequent behavior. A measured total
response does not by itself identify the controller gain separately from the
worker dynamics.

The proposed path is a desired behavioral change, a concrete prompt intervention,
and a measured response curve. Exact wording is a candidate actuator, not a
precalibrated magnitude. More emphatic wording, extra tokens, numeric labels, and
the model's own asserted confidence do not define correction units. A numeric
P/I implementation also needs explicit error and intervention coordinates.

## Research grounding

[Xie et al., ICLR 2022](https://arxiv.org/abs/2111.02080) model in-context learning
as inference of a latent concept under a mixture-of-HMMs pretraining assumption,
with synthetic experiments. This supports investigating inferred task state;
it does not establish the proposed three-part model for our agentic worker.

[Dong et al., 2026, section 3.1](https://arxiv.org/html/2601.06403v1#S3.SS1)
contrast target-prompt and default-prompt logits using
`z_target + alpha * (z_target - z_default)` at each shared generated prefix.
Alpha scales a defined logit difference; zero means ordinary target-prompt
decoding. Their experiments also show task-dependent optima and degradation
under excessive amplification. This is a decoding intervention requiring both
logit streams, not a numeric field inside an ordinary prompt or a PI stability
result. It illustrates a possible definition of strength at a different control
boundary.

[Heyman and Vandeputte, 2026](https://arxiv.org/html/2605.03907v1) study how prompt
interventions vary across token positions. Their replacement models rely on
explicit assumptions that do not hold universally. This cautions against
modeling every prompt as one constant push throughout generation.
