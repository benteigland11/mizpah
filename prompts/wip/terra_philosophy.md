# Terra: what the map is for

Terra makes a project whose answer cannot be verified into one whose evidence can. The map is not a record of work; it is a structure of confidence: verifiable pieces, each measured, composed into a belief about the whole. Every rule below follows from that.

## Evidence, not assertion

A known is believed because of its runs, never because of who wrote it. A reading comes from the source through code that reads it; a constant, a guess, or a value chosen to satisfy the gate is not evidence and poisons everything composed on top of it. A wrong reading is repaired by voiding the run, never by editing the value.

## Two kinds of confidence, two kinds of repetition

Confidence in a known comes from one of two places, and the map is built to tell them apart.

- **A variable quantity** — one whose honest reading differs from run to run (a timing, a rate, a measurement with noise) — earns confidence by **repeating the same probe**. The runs are samples; the known carries their distribution (n, mean, std), and that distribution is what a consumer uses: not the number, but the number with its spread. Running such a probe once and calling it known is the error the ladder exists to refuse.
- **A determined quantity** — one whose honest reading is the same every time (a file's note count, a parse, a boolean about an artifact as it is) — earns nothing from repetition. Three identical runs of one probe are one reading written three times. Its confidence comes from **corroboration: a different probe, by a different method, reading the same fact** — a second instrument that could disagree and does not. Two methods agreeing within tolerance is a stronger claim than a hundred runs of one.

Nobody declares which kind a quantity is; the runs show it. The system reads the runs as they come: readings that repeat exactly are a determined quantity, and the ladder stops — more runs add nothing, and the next rung is a second method. Readings that differ are a variable quantity, and the ladder keeps sampling until the spread has settled, or gives up when it will not. The worker's part is to recognize what its runs are saying: a probe that cannot give a determined result is telling it the quantity is variable, and the reading to hand on is the distribution, not one of its samples. A worker who repeats a determined probe to reach `n=3` has performed the ceremony and gained nothing; a worker who runs a variable probe once has a number with no spread.

## Composition

Knowns compose. A formula known is built from other knowns' values; a relation is a curve of readings; a known can depend on another (`terra known depend`) so that when the dependency moves, the dependent goes stale. Confidence composes too: a known cannot be more certain than what it is built from. This is how a large unverifiable claim is held up by many small verifiable ones, and why a dishonest reading anywhere is not local.

## The gate is mechanical

Green means every entry the brief owes is covered by an adopted known at the confidence bar and nothing owed is open. Red names what is missing. It reads the map; it does not read arguments. If the gate is wrong the map is wrong, and the map is fixed by evidence.

## Scopes

Work happens on a task map; belief lives on the project map. Adoption is the border: a known crosses it only at the bar, with its runs, and is then consumed by every later task. Reads fall through from task to project; writes stay local. A session map is where a risky reading can be taken without moving the project's belief.
