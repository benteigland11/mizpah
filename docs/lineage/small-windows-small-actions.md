---
name: small-windows-small-actions
description: "Design commitment (2026-09-17) — the focused harness runs 60–90K windows, which implies bounded tool actions (~1.5–2k tokens per turn, args + result + response); large writes are to be prevented at the source, not just projected away"
metadata: 
  node_type: memory
  type: project
  originSessionId: 58c394a7-ce65-4cf2-b511-2ca73894f30d
  modified: 2026-09-17T02:38:31.940Z
---

On 2026-09-17 the user committed the focused harness to **small context windows (60–90K tokens) and therefore small tool actions**. "If we are going to commit to small context windows, some of that means committing to smaller tool actions." "Just because an LLM can do it doesn't mean we should."

**Why:** v4 pilot evidence showed the worker's own heredoc file rewrites (2–4k chars each, files rewritten 3×) dominated both its history channel and the controller's evidence envelope (41% of the 10-turn window was tool-call arguments; 25% was `applied_input` echoing the previous result). Retained reasoning was another 42% of the rendered worker prompt (fixed in session leaf 1.5.0 / blueprint 0.6.0, `reasoning_retention: latest`). The compaction-window sweep ([[compaction-noise-vs-error]]) supports many small steps in short windows as the low-noise regime.

**How to apply:**
- Prefer bounding actions at the source (argument-size bound with feedback + `write`/`edit` tools with unique-anchor semantics like `project_edit`) over only projecting large arguments out of history. Do both: bound first, then project completed-call arguments to `wrote <file> (<n> chars)` in worker history and controller evidence.
- Treat the continuity fixture (mandated 40k unfiltered packet dumps) as a compaction stress test, not a system benchmark — it forces violation of this rule.
- Any v5 arm should measure total worker tokens, not just per-turn size, since bounded actions cost more turns.
