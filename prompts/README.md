# prompts

The instructions each seat runs on, as many small Markdown files composed into one prompt per seat. Everything
here is fed to an agent; notes on how a mechanism is meant to work live in `design/`. One file, one thing: the
glossary (`glossary.md`), blocks that say how one thing works (`unknowns.md`, `probes.md`, `map.md`,
`route.md`), and a seat's own policy (`policy_worker.md`, `policy_controller.md`, `policy_reviewer.md`). A seat's
prompt is the glossary, the blocks that seat needs, then its policy, composed at load and never pasted.
`composed/` holds the result for reading; the engine does not load it.

A piece that is not settled yet can live in `wip/` (there are none today): the composer takes it from there,
with a note, so the loop runs while it is written. It moves up when it is finished: written in the glossary's
words, one meaning per term, nothing in it the engine does not do.

`messages/` are the single messages the engine sends a seat at a moment (a handoff, a red gate, a correction),
one file each.

A file's name says who gets it: `name[_seat].md`. No seat tag (`probes.md`): the worker and the controller,
the two seats that run the loop and share its vocabulary; a seat tag (`probes_worker.md`, `probes_controller.md`)
goes to that seat only, composed right after the shared piece of the same name. Their order is `order.txt`, one
name per line.

The reviewer is a different kind of seat: it reads files and answers one question. Nothing reaches it by
default. Its prompt is `order_reviewer.txt`, an explicit list of exactly what it gets (the glossary, unknowns,
probes, `probes_reviewer.md`, `policy_reviewer.md`): a `_reviewer` tag marks a piece written for it, and only that list
includes it. The engine composes at load (`mizpah.prompts.compose`).

The Deputy is the other seat of that kind, kept apart in `deputy/`: its `order.txt` is its list, and a name on
it is looked up in that folder first, then here, so it gets the glossary and nothing else of this level. Its
README says the rest.
