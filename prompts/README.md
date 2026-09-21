# prompts

The instructions each seat runs on, as many small Markdown files composed into one prompt per seat. Everything
here is fed to an agent; our own design notes live in `design/`. One file, one thing: a glossary of terms
(`glossary.md`), a block that says how one thing works (`unknowns.md`, `terra_philosophy.md`), a seat's own policy
(`worker_policy.md`, `route_policy.md`, `eval_policy.md`, `checkin_policy.md`). A seat's prompt is the
glossary, the blocks that seat needs, then its policy — composed at load, never pasted.

`wip/` holds what is not settled: the old policies as they run today, and blocks still being written. A file
moves up out of `wip/` when it is finished — written in the glossary's words, one meaning per term, nothing in
it the engine does not do. 

A file's name says who gets it: `name[_seat].md`. No seat tag (`probes.md`) — the worker and the controller,
the two seats that run the loop and share its vocabulary; a seat tag (`probes_worker.md`, `probes_controller.md`)
— that seat only, composed right after the shared piece of the same name. Their order is `order.txt`, one
name per line.

The reviewer is a different kind of seat: it reads files and answers one question. Nothing reaches it by
default. Its prompt is `order_reviewer.txt`, an explicit list of exactly what it gets (the glossary, unknowns,
probes, `probes_reviewer.md`, `policy_reviewer.md`) — a `_reviewer` tag marks a piece written for it, and only that list
includes it. Both order files are written when the pieces are.

Terms are used in the glossary's sense and no other, by every seat. A block explains; a policy instructs; the
glossary names. Engine configs name the policy files by path (`*_policy_file`); the engine reads them from here.
