# Unknowns

## Parts

- **id** — a slug; the quantity a probe reports for it.
- **type** — one of the five below; it decides what a resolved reading is.
- **claim** — the sentence a reading of this quantity makes true or false.
- **evidence** — the reading a probe takes, and from which source. The source is the artifact or data the probe reads or runs; never a file written to say the answer.
- **served** — what the unknown is evidence for: a brief need or deliverable (the controller's), or the unknown it decomposes (the worker's, on its task map). One that cites nothing is refused.

## The five types

- **number** — a value with an optional unit. Runs are samples; the known carries mean ± std over n.
- **boolean** — true or false. Runs must agree.
- **label** — a name the data answers with (a category, a verdict word). Runs must agree.
- **formula** — an expression over named variables, each bound to a run quantity or a live known. Composed from evidence already on the map, not measured directly.
- **relation** — a curve F(x): the x quantity and its unit are named; readings are points along it.
