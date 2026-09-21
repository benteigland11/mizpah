# Unknowns

What the worker does with one it is handed — and what the reviewer holds it to.

- Its probe is `<id>_probe`, one per unknown, and `measure.py` returns `{"<id>": value}` — a value of the unknown's type, read from the source its evidence names.
- The claim says what that value makes true or false. A reading that would come out the same for a wrong artifact resolves nothing.
- The type says what a resolved reading is: a number gets a distribution over runs; a boolean or label a verdict the runs agree on; a formula is composed from knowns on the map; a relation is points along a curve.
