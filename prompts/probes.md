## Probes

- A probe is a measurement of one source: it declares the quantities it reads and reports exactly those, `{"<quantity>": value}` each. One run links to every unknown it measures.
- A reading comes from the source through code that reads it — never a constant, a guess, or a value chosen to pass. A reading that would come out the same for a wrong artifact resolves nothing.
- The reviewer's bar does not move; among the readings that clear it, the one the pipeline gives cheapest is the one to take.

Examples of one probe, several quantities, read from its declared source:

- A rendered audio file: `duration_s` (number), `peak_dbfs` (number), `is_silent` (boolean) — one decode, three readings.
- A test suite's run: `tests_passed` (number), `suite_green` (boolean), `slowest_test` (label) — one `pytest` invocation, parsed once.
- An engraved score against its performance: engrave with the renderer's own MIDI output, then `notes_match` (boolean) and `bars` (number) from comparing that output to the source — the artifact's own pipeline as the source, not its rendered surface.
- A curve: `latency_ms` against `payload_kb` (relation) — one probe run per x, the readings its points.
