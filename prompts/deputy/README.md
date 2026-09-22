The Deputy's prompt, kept apart from the loop's seats. `order.txt` is its list, one name per line; a name is
looked up here first, then in the folder above — which is how it gets the glossary, the one piece every seat
needs, and nothing else from there. The pieces keep the house shape: a block that explains (`brief.md`,
`environment.md`), a `_deputy` tail that instructs (`brief_deputy.md`, `environment_deputy.md`), the seat's
own words after the glossary (`glossary_deputy.md`), and the seat itself (`policy_deputy.md`). Composed by
`mizpah.prompts.compose('deputy', …)` like the others; `README.md` is not part of the prompt. `$administrator` in a piece is the signer's title from the app's Settings (`signer_title` in `app.json`),
`Administrator` when nothing is set; `$principal` is the signer by name and title ("Ben, the Administrator",
or "the Administrator" with no name). Both are filled at compose time and read fresh every turn. Rules that hold
across it: teach a method, never a number; the Administrator's words and this text are the only instructions
the Deputy takes.
