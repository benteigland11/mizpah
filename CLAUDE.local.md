# Machine-specific notes (not committed)

- Research workspace: `/home/Vinscen/decisions`. `docs/lineage/` holds copies frozen at the fork; the
  originals and full campaign data (journals, fixtures, snapshots) stay there.
- Design canvas (Flywheel Studio): https://claude.ai/artifact/47xMRDgkAUvhp5siPbw6jq; the agreed graph
  and answered questions live in its `design/main` document.
- Local model: llama.cpp Gemma-4-26B-A4B behind a manager on :58000 (`/start`, `/status/58081`); launch
  request and pid history in `/home/Vinscen/decisions/artifacts/focused-harness/gemma4-gpu0/`.
- Local providers (bonsai :58080, gemma :58081, nemotron :58082, ornith :58083) are in
  `~/.config/mizpah/config.json`.
