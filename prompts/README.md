# prompts

The instructions each seat runs on, as many small Markdown files composed into one prompt per seat. Everything
here is fed to an agent; our own design notes live in `design/`. One file, one thing: a glossary of terms
(`glossary.md`), a block that says how one thing works (`unknowns.md`, `terra_philosophy.md`), a seat's own policy
(`worker_policy.md`, `route_policy.md`, `eval_policy.md`, `checkin_policy.md`). A seat's prompt is the
glossary, the blocks that seat needs, then its policy — composed at load, never pasted.

Terms are used in the glossary's sense and no other, by every seat. A block explains; a policy instructs; the
glossary names. Engine configs name the policy files by path (`*_policy_file`); the engine reads them from here.
