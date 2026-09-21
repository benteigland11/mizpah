# Unknowns: the specifics

## Parts

- **id** — a slug. Its probe is `<id>_probe`; the probe returns `{"<id>": value}`.
- **type** — number, boolean, label, formula or relation (below).
- **quantity** — the stable name of the measure (number, boolean, label): what the probe returns and what other knowns depend on. `--quantity`.
- **claim** — the sentence a reading of this quantity makes true or false.
- **evidence** — the reading a probe takes, and from which source. The source is the artifact or data the probe reads or runs; never a file written to say the answer.
- **entry served** — the brief need or deliverable the unknown is evidence for. An unknown that cites none is refused.
- **blocks_build** — whether the gate treats it as owed (default yes).
- **within** — the tolerance for two methods to count as agreeing (`5%`, `0.5`); carried to the known.

## The five types

- **number** — a value with an optional unit. Runs are samples; the known carries mean ± std over n.
- **boolean** — true or false. Runs must agree.
- **label** — a name the data answers with (a category, a verdict word). Runs must agree.
- **formula** — an expression over named variables, each bound to a run quantity or a live known (`--expression`, `--var`). Composed from evidence already on the map, not measured directly.
- **relation** — a curve F(x): the x quantity and its unit are named (`--x-quantity`, `--x-unit`); readings are points along it.

## What is not an unknown

- A file written to describe the work (notes, plan, write-up): a file never reaches another project; the library does.
- A need about the method ("improve the procedure"): its reading is the procedure itself — one boolean whose source is the named procedure.
- An instrument. A probe or a widget measures; it is never itself a finding.
