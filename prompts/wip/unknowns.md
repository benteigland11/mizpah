# Unknowns

An unknown is a named gap between the brief and the map: one question, one typed quantity, one probe. It is minted by the controller citing the brief entry it serves, and resolved by the worker through runs of its probe. This file says what an unknown is made of and how it becomes a known; the glossary names the terms.

## Parts

- **id** — a slug; the probe is `<id>_probe` and the probe returns `{"<id>": value}`.
- **type** — one of the five below.
- **quantity** — the stable name of the measure (number, boolean, label): what the probe returns and what other knowns depend on. `--quantity`.
- **claim** — what the value means for the brief: the sentence a reading of this quantity makes true or false.
- **evidence** — what resolves it: the reading a probe takes, from which source. A source is the artifact or data the probe reads or runs; never a file the worker wrote to say the answer.
- **entry served** — the brief need or deliverable this unknown is evidence for. An unknown that cites none is refused.
- **blocks_build** — whether the gate treats it as owed (default yes).
- **within** — the tolerance for two methods to count as agreeing (`5%`, `0.5`); carried to the known.

## Types

- **number** — a value with an optional unit. Runs are samples: the known carries mean ± std over `n`; n=1 cannot be high confidence.
- **boolean** — true or false. Runs must agree.
- **label** — a name the data answers with (a category, a verdict word). Runs must agree.
- **formula** — an expression over named variables, each bound to a run quantity or a live known (`--expression`, `--var`). The value is composed from evidence already on the map, not measured directly.
- **relation** — a curve F(x): the x quantity and its unit are named (`--x-quantity`, `--x-unit`); readings are points along it.

## From unknown to known

1. **Probe** — one per unknown, a directory with `measure.py` returning `{"<id>": value}` from the named source. Validated (`terra probe validate`) before it can run.
2. **Run** — a stamped execution with its reading (`terra probe run`, `terra run`). A run is the only thing a worker may cite.
3. **Graduate** — the unknown becomes a known on the task map with its runs linked; `n` and confidence follow from the runs (`terra known link-run`, `promote`).
4. **Ladder** — run the probe again until the confidence bar is met (`terra known ladder`): low → med → high. The bar for adoption is med; n=1 cannot be high.
5. **Adopt** — promote the known one hop up, from the task map to the project map (`terra known adopt --from <map>`). Adoption is what makes it believed: every later task consumes it, the controller routes on it, the gate counts it.
6. **Complete** — `terra route complete <task> --run <run> --known <known>` closes the task citing them.

## Repair

A known born from a bad reading is repaired by evidence, never by editing: `terra run void <run> --reason …` drops the run, the value recomputes, and a fixed probe's run is linked. A known whose every run is voided is empty again. A `False` or an unwelcome number is a valid reading; the artifact is fixed and remeasured, the reading is never forced.

## What is not an unknown

- A file the worker would write to describe its work (notes, plan, write-up): a file never reaches another project; the library does.
- A need about the method ("improve the procedure"): its reading is the procedure itself, one boolean whose source is the named procedure.
- An instrument (a probe, a widget): an instrument measures; it is never itself a finding.
