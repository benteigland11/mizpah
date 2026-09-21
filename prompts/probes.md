## Probes

- A probe is a measurement of one source: it declares the quantities it reads and reports exactly those, `{"<quantity>": value}` each. One run links to every unknown it measures.
- A reading comes from the source through code that reads it — never a constant, a guess, or a value chosen to pass. A reading that would come out the same for a wrong artifact resolves nothing.
- The reviewer's bar does not move; among the readings that clear it, the one the pipeline gives cheapest is the one to take.
