## Non-verifiable outcomes

A **need** written as a standard — the requestor's taste — is something no **probe** can read directly. You reach it through the **map**, in four moves:

1. **Break each need into verifiable pieces.** List what a person holding the standard would notice, and cut each into a **quantity** a probe can read off the **deliverable** — a number, boolean or label. Anything you cannot cut that far is a hole in the map.
2. **Set a direction for each piece.** Decide which way is better and the bar a strong result clears, and say why. The library's procedures record what practitioners attend to (`playbook search`, `playbook load`); take your direction from them.
3. **Separate collection from targets.** A **collection** unknown gathers one reading; name it `collect_<quantity>`. A **target** is a `formula` unknown over collection knowns with your bars, citing its need. Mint collection first; once its knowns exist, mint the target and route it low — the worker links a run and Terra evaluates the expression over the knowns:
   `{"id": "<quality>", "type": "formula", "expression": "x >= <bar> and y <= <bar>", "vars": {"x": "known:collect_<quantity>", "y": "known:collect_<quantity>"}, "cites": "need:<n>"}`
4. **Judge, then send back.** Read the collection's values (`terra known show`) before you compose; an implausible one is a hole to route. A target that reads false reopens the **work order** that built the deliverable, with the delta. Re-measuring, or moving the bar, is not an answer.

A need is met when its targets are true. The **gate** green on collection alone means the material is in, not that the outcome is reached.
