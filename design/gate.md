# The gate

The gate is the passing grade. It is one mechanical check over the map (`terra gate`): green when the map carries no debt, red with the debts named. It reads the map and nothing else — not the brief's prose, not a worker's words, not a controller's judgment. If the gate is wrong, the map is wrong, and the map is fixed by evidence. Nobody argues with it and nobody overrides it.

## What it asks

The gate scans every map — the project map and every task map — so a debt on a sub-map cannot hide, then the design layer. It is green only when all of these hold:

1. **No open unknown that blocks.** Every unknown minted against the brief has become a known. An open unknown is the plainest debt: a question asked and not answered. (An *assumption* — a provisional value someone chose — does not fail the gate, but is called out loud: work built on it is conditional.)
2. **Every known is backed.** A known's value rests on live runs. Void the runs and the known is unbacked: a belief with no evidence under it.
3. **Nothing is stale.** A known declares what it depends on — another known, an input file, an artifact. When a dependency moves, the dependent is stale until it is re-derived. A calculation over stale knowns is stale with them.
4. **Independent methods agree.** When two probes by different methods read the same fact, they agree within the declared tolerance. Disagreement means one instrument is wrong, and nothing built on that known can be trusted until it is resolved. Two methods with no tolerance declared are *unjudged* — also a debt: say how close is close enough.
5. **Counts do not average.** A count whose live runs read different values is a disagreement, not a mean; the runs that no longer hold are voided.
6. **Formulas hold.** A known composed by formula from other knowns is checked as claimed.
7. **Evidence plans are complete.** A plan that says "prove A and B" or "A then B" has every leg satisfied.
8. **Nothing conditional stands as fact.** A known or calculation that rests on an assumption is conditional, not believed.
9. **The design is not red.** A design parameter admitted from a known goes red when that known moves; an artifact attached to the design goes red when it is not regenerated. Red design fails release.

Some things are surfaced but do not fail it: an accepted spread between methods (`known accept-spread`, a band taken on as uncertainty), a retired known kept as history, an active assumption. These are *notices* — the gate passes and says so out loud.

## What it does not ask

The gate never asks whether a reading is honest, whether a probe would fail a wrong artifact, whether a widget validates, whether a walk was ticked, or whether the work was done the right way. Those are questions about the worker's claim, and they are answered *before* a claim reaches the map — by the reviewer, by the honesty checks at `route complete`, by the library's validators. The gate assumes the map is honest and asks whether it is complete and consistent. Keep the two apart: a gate that also judged honesty would be arguing, and a claim check that also judged completeness would be a second gate.

## Two scopes, one gate

- **The worker** works on its own task map. Its job is to make the gate green *there*: the unknowns it was handed become knowns at the bar, backed, unstale, agreed. Then it adopts them one hop up. Adoption is the border: a known crosses only at med or better, with its runs, and is then believed by the whole project.
- **The controller** reads the gate on the project map. Red is its to-do list: every violation names an unknown to route, a stale known to re-derive, a disagreement to resolve. Green is done — there is nothing owed, and the loop stops. The controller does not decide the project is finished; the gate does.

## Confidence

A known enters the gate's count only at **med** or better. Low is not a resting state; it means the evidence is on its way — one run of a variable quantity, a spread not yet settled, methods still in disagreement. Med is the floor of belief: for a variable quantity, enough samples that its spread is known; for a determined one, its reading, once. High is corroboration — a second method agreeing within tolerance — and is the same bar for every kind. `promote` cannot lift a known past what its runs derive; there is no force.

## Reading a red gate

Each violation carries the map it is on, its kind, and why, in one line:

```
[unknown_blocking] b_romantic_piano_piece/score_publisher_playable: unknown still open: A pianist reading `piece.pdf` would play the piece as written and find nothing to correct
[known_stale]      global/piece_pdf_built: known is stale: file:piece.pdf changed after the last run
[methods_disagree] t_compose/tempo_bpm: methods disagree (spread=6.0 vs tolerance=2%)
```

The first is routed to a worker. The second is re-measured. The third is resolved by voiding the wrong instrument's runs. None of them is discussed.
