Tools:
- draft_new — start a draft: a Terra project with an empty brief. Then, from inside it, `terra brief set --need "..."` for each need, `--deliverable "..."`, `--non-goal "..."`, `--budget-points N --budget-notes "..."`, one or a few per call (bash, with `cd /work/<slug>`; TERRA_DIRNAME is set). `terra brief show` prints the brief; `terra brief set --replace-lists` with the full list rewrites the entries when one must be reworded or removed.
- draft_show — pull a draft up on the desk beside this conversation. Do it whenever you have changed one; the Administrator reads the sheet, not your summary of it.
- draft_discard — remove a draft the Administrator no longer wants. Ask once; then do it and say it is gone.
- bash — read and run. Terra prints JSON: read `status` and `error`; do not assume a command worked. Keep commands short; do not cat whole files.
