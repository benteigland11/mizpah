## Probes

- A probe is an instrument, like a multimeter on a bench: put on one source, it reads out the quantities it is built to read — voltage, current, resistance; duration, peak, silence. It declares those quantities and reports exactly them, `{"<quantity>": value}` each. It knows nothing of what the readings are for.
- The readings are what get composed into evidence. One run links to every unknown whose quantity it reports; the same instrument serves a different question tomorrow. Build the instrument general and put the question in the unknown.
- A reading comes from the source through the instrument — never a constant, a guess, or a value chosen to pass. An instrument that would read the same on a wrong artifact is not measuring it.
- The reviewer's bar does not move; among the readings that clear it, the one the pipeline gives cheapest is the one to take.
- The instrument's workings live in widgets; `measure.py` is the few lines that put them on this source and name the quantities. A reading computed inline in a probe is lost when the work order ends; the same reading as a widget function is an instrument the next bench installs.
