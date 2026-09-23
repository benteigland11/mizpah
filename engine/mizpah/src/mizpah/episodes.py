"""Where a worker got stuck and what got it unstuck, read off a work order's trace.

Two kinds of episode, both marked in the trace already:

- a command family that failed again and again, then passed (the same script, the same terra verb): the errors are
  the symptom, the passing call is the fix;
- a gate that went red and later green: the red problems are the symptom, the turns between are the repair.

One failure then a pass is the worker solving it unaided and is not an episode: what is worth a case is what cost
turns. Loop bookkeeping is left out (playbook walks and checklists, harness rejections of oversized payloads): the
harness says what those are itself.

The miner only finds and cuts; a writer (the worker at green, or a model over past traces) says what it was."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

# Consecutive failures of one family before its pass that make a stuck moment.
STUCK_FAILURES = 2
SLICE_CHARS = 14000
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")
_BOOKKEEPING = ("playbook",)
# Misses of looking and editing (a path that is not there, an edit whose old text moved): the tool says exactly
# what was wrong, and the next call fixes it; not a problem a case can teach.
_LOOKING = {"read", "write", "edit", "ls", "cat", "grep", "find", "head", "tail", "sed", "rg", "wc", "for"}
# Gate problems about the playbook's own walks (a checklist left unticked, walks nested too deep) are bookkeeping.
PLAYBOOK_PROBLEM = re.compile(r"\.playbook/|procedure walks? (?:are|is) open")


def _args(fn: dict[str, Any]) -> dict[str, Any]:
    raw = fn.get("arguments")
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {"raw": str(raw)}
    return value if isinstance(value, dict) else {}


def read_turns(journal: Path) -> list[dict[str, Any]]:
    """The worker's turns in order: what it said, each call it made and what came back."""
    out: list[dict[str, Any]] = []
    if not journal.exists():
        return out
    with open(journal, errors="replace") as handle:
        for line in handle:
            if '"worker_turn"' not in line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("event_type") != "worker_turn":
                continue
            p = event.get("payload") or {}
            response = p.get("response") or {}
            results = {r.get("call_id"): r for r in p.get("tool_results") or [] if isinstance(r, dict)}
            calls = []
            for tc in response.get("tool_calls") or []:
                fn = tc.get("function") or tc
                args = _args(fn)
                name = str(fn.get("name") or "")
                text = str(args.get("command") or "") if name == "bash" else json.dumps(args, ensure_ascii=False)
                result = (results.get(tc.get("id")) or {}).get("result") or {}
                calls.append(dict(name=name, text=text, status=result.get("status"), exit_code=result.get("exit_code"),
                                  out=_output(result)))
            out.append(dict(ordinal=len(out), turn=p.get("turn"), at=event.get("created_at"),
                            content=str(response.get("content") or ""), calls=calls))
    return out


def _output(result: dict[str, Any]) -> str:
    parts = [str(result.get(k) or "") for k in ("stderr", "detail", "error", "stdout")]
    return "\n".join(p for p in parts if p.strip())


def failed(call: dict[str, Any]) -> bool:
    if call.get("status") not in (None, "ok"):
        return True
    return call.get("exit_code") not in (None, 0)


def family(call: dict[str, Any]) -> str:
    """What counts as trying the same thing again: the typed tool, or a shell command's program and its first
    argument that names what it runs (a script, a module, a terra or cartograph verb)."""
    if call["name"] != "bash":
        return call["name"]
    text = call["text"].strip()
    # The last command of a chain decides the exit code; cd and environment settings in front are not the command.
    segment = re.split(r"\s*(?:&&|;|\|\|)\s*", text)[-1] if text else ""
    try:
        words = shlex.split(segment)
    except ValueError:
        words = segment.split()
    words = [w for w in words if not _ENV.match(w)]
    while words and words[0] in ("cd", "timeout", "env", "time", "uv", "run"):
        words = words[2:] if words[0] in ("cd", "timeout") else words[1:]
    if not words:
        return "bash"
    head = words[0].rsplit("/", 1)[-1]
    if head.startswith("python"):
        rest = [w for w in words[1:] if not w.startswith("-") or w == "-m"]
        if rest[:1] == ["-m"] and len(rest) > 1:
            return "python -m " + rest[1]
        return "python " + (rest[0].rsplit("/", 1)[-1] if rest else "")
    if head in ("terra", "cartograph", "playbook", "casebook", "git"):
        verbs = [w for w in words[1:3] if re.fullmatch(r"[a-z][a-z-]*", w)]
        return " ".join([head] + verbs[:2])
    return head


def error_episodes(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    found: list[dict[str, Any]] = []
    for t in turns:
        for call in t["calls"]:
            fam = family(call)
            head = fam.split(" ", 1)[0]
            if head.startswith(_BOOKKEEPING) or head in _LOOKING or call.get("status") == "rejected":
                continue
            if failed(call):
                run = runs.setdefault(fam, dict(family=fam, start=t["ordinal"], failures=[], turns=set()))
                run["failures"].append(dict(ordinal=t["ordinal"], text=call["text"], out=call["out"]))
                run["turns"].add(t["ordinal"])
                continue
            run = runs.pop(fam, None)
            if run and len(run["failures"]) >= STUCK_FAILURES and len(run["turns"]) >= 2:
                found.append(dict(kind="error", family=fam, start=run["start"], end=t["ordinal"],
                                  cost=t["ordinal"] - run["start"] + 1, failures=len(run["failures"]),
                                  symptom=_clip(run["failures"][0]["out"], 1200),
                                  last_error=_clip(run["failures"][-1]["out"], 800),
                                  passed=dict(text=_clip(call["text"], 1200), out=_clip(call["out"], 600))))
    return found


def _result_files(task_dir: Path) -> list[Path]:
    archived = sorted(task_dir.glob("result.*.json"), key=lambda p: int(p.name.split(".")[1]) if p.name.split(".")[1].isdigit() else 0)
    last = task_dir / "result.json"
    return archived + ([last] if last.exists() else [])


def gate_episodes(task_dir: Path, turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Red rounds followed by a green one, in each report the work order wrote (a reopen archives the earlier one)."""
    by_turn: dict[Any, int] = {}
    for t in turns:
        by_turn.setdefault(t["turn"], t["ordinal"])
    found: list[dict[str, Any]] = []
    for path in _result_files(task_dir):
        try:
            report = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        red: dict[str, Any] | None = None
        for rnd in report.get("rounds") or []:
            gate = rnd.get("gate")
            if gate is False:
                real = [p for p in rnd.get("problems") or [] if not PLAYBOOK_PROBLEM.search(str(p))]
                if real and red is None:
                    red = dict(turn=rnd.get("turns"), problems=real)
            elif gate is True and red is not None:
                start, end = by_turn.get(red["turn"]), by_turn.get(rnd.get("turns"))
                if start is not None and end is not None and end > start:
                    found.append(dict(kind="gate", start=start, end=end, cost=end - start,
                                      symptom=_clip("\n".join(red["problems"]), 1500), report=path.name))
                red = None
    return found


def _clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit // 2] + "\n…\n" + text[-limit // 2 :]


def cut(turns: list[dict[str, Any]], start: int, end: int, limit: int = SLICE_CHARS) -> str:
    """The turns of an episode as a writer reads them: what the worker said, what it ran, what came back."""
    lines: list[str] = []
    for t in turns[start : end + 1]:
        lines.append(f"## turn {t['turn']}")
        if t["content"].strip():
            lines.append(_clip(t["content"].strip(), 600))
        for call in t["calls"]:
            mark = "FAILED" if failed(call) else "ok"
            lines.append(f"$ [{call['name']}] {_clip(call['text'], 700)}")
            if call["out"].strip():
                lines.append(f"-> {mark}: {_clip(call['out'].strip(), 700)}")
            else:
                lines.append(f"-> {mark}")
    text = "\n".join(lines)
    return _clip(text, limit)


def mine(task_dir: Path, where: str = "") -> list[dict[str, Any]]:
    """Every stuck-then-unstuck episode of one work order, each with the cut of the trace it spans."""
    turns = read_turns(task_dir / "events" / "session.jsonl")
    episodes = error_episodes(turns) + gate_episodes(task_dir, turns)
    for e in episodes:
        e["where"] = where or task_dir.name
        e["source"] = f"{task_dir}/events/session.jsonl turns {turns[e['start']]['turn']}-{turns[e['end']]['turn']}"
        e["trace"] = cut(turns, e["start"], e["end"])
    episodes.sort(key=lambda e: e["start"])
    return episodes
