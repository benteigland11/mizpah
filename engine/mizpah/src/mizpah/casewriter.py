"""Turn a mined episode into a case: the worker seat's model reads the stretch of trace and says what it was.

At green the worker writes its own cases; this is the same request made over past traces, to seed the casebook.
One episode at a time and in order, so a later episode can join a case an earlier one filed.

    python -m mizpah.casewriter --config <engine config> <episodes.jsonl> <outcomes.jsonl>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from casebook import cases as casebook_cases
from casebook import ops as casebook

from . import prompts
from .worker import load_config

WRITER_TOKENS = 4096


def kind_line(episode: dict[str, Any]) -> str:
    if episode["kind"] == "error":
        return (f"The worker ran `{episode['family']}` and it failed {episode['failures']} times over "
                f"{episode['cost']} turns before it passed. The first failure said:\n\n{episode['symptom']}")
    return f"The gate went red with these problems, and green {episode['cost']} turns later:\n\n{episode['symptom']}"


def similar_lines(episode: dict[str, Any]) -> str:
    query = " ".join([episode.get("family") or "", episode["symptom"][:600]])
    hits = casebook.search(query, 4).get("hits") or []
    if not hits:
        return "(none)"
    return "\n".join(f"- {h['id']}: {h['problem']} — {h['diagnosis']}" for h in hits)


def _json_object(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def model_client(config: dict[str, Any], seat: dict[str, Any] | None = None) -> Any:
    from .controller import model_client as seat_client
    return seat_client({**config, "controller": seat or config["worker"]})


def seat_for(config: dict[str, Any], episode: dict[str, Any]) -> dict[str, Any]:
    """The worker seat that wrote the episode, when the episode names it (`worker`: provider, subscription,
    generation): the model that was stuck is the one that says what it was. Otherwise the config's worker."""
    return {**config["worker"], **episode["worker"]} if episode.get("worker") else config["worker"]


def ask(client: Any, config: dict[str, Any], messages: list[dict[str, str]], seat: dict[str, Any] | None = None) -> str:
    from cg.backend_persistent_model_session_python.src.persistent_model_session import parse_turn
    payload = dict((seat or config["worker"]).get("generation") or {}, messages=messages, max_tokens=WRITER_TOKENS)
    response = client.complete(payload, "casebook")
    return str(parse_turn(response).message.get("content") or "")


def file_case(decision: dict[str, Any], episode: dict[str, Any]) -> dict[str, Any]:
    item = casebook_cases.instance(episode["where"], str(decision.get("symptom") or episode["symptom"]),
                                   str(decision.get("change") or ""), episode["cost"], episode.get("source", ""))
    if decision.get("into"):
        try:
            return casebook.add("", "", "", item, into=str(decision["into"]))
        except KeyError:
            return dict(ok=False, refused=[f"there is no filed case {decision['into']!r}; join one listed, or file a new case"])
    # The writer saw the look-alikes and chose a new case: file it as new.
    return casebook.add(str(decision.get("problem") or ""), str(decision.get("diagnosis") or ""), str(decision.get("fix") or ""),
                        item, touches=[str(t) for t in decision.get("touches") or []], new=True)


def write(client: Any, config: dict[str, Any], episode: dict[str, Any], seat: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask, file, and on a refusal ask once more with what was refused."""
    request = prompts.message("case_writer", kind=kind_line(episode), similar=similar_lines(episode),
                              where=episode["where"], cost=episode["cost"], trace=episode["trace"])
    messages = [dict(role="user", content=request)]
    outcome: dict[str, Any] = {}
    for attempt in range(2):
        reply = ask(client, config, messages, seat)
        decision = _json_object(reply)
        if decision is None:
            outcome = dict(ok=False, error="no JSON object in the reply", reply=reply[:800])
        elif not decision.get("case"):
            return dict(ok=True, case=False, why=decision.get("why"))
        else:
            filed = file_case(decision, episode)
            outcome = dict(filed, decision=decision)
            if filed.get("ok"):
                return outcome
        messages += [dict(role="assistant", content=reply),
                     dict(role="user", content="The casebook refused that: " + json.dumps(outcome.get("refused") or outcome.get("error"))
                          + ". Reply again with the corrected JSON object only.")]
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mizpah.casewriter")
    parser.add_argument("--config", required=True)
    parser.add_argument("episodes")
    parser.add_argument("outcomes")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    clients: dict[str, Any] = {}
    done = set()
    out_path = Path(args.outcomes)
    if out_path.exists():
        done = {json.loads(line).get("source") for line in out_path.read_text().splitlines() if line.strip()}
    episodes = [json.loads(line) for line in Path(args.episodes).read_text().splitlines() if line.strip()]
    written = 0
    with open(out_path, "a") as out:
        for episode in episodes:
            if episode.get("source") in done:
                continue
            if args.limit is not None and written >= args.limit:
                break
            seat = seat_for(config, episode)
            key = json.dumps([seat.get("subscription"), seat.get("generation")], sort_keys=True)
            try:
                if key not in clients:
                    clients[key] = model_client(config, seat)
                outcome = write(clients[key], config, episode, seat)
            except Exception as exc:   # one episode's failure is not the batch's
                outcome = dict(ok=False, error=f"{type(exc).__name__}: {exc}")
            outcome.update(source=episode.get("source"), where=episode["where"], kind=episode["kind"], cost=episode["cost"],
                           writer=(seat.get("generation") or {}).get("model"))
            out.write(json.dumps(outcome, ensure_ascii=False) + "\n")
            out.flush()
            written += 1
            print(episode["where"], f"[{outcome['writer']}]", "->", "case " + str(outcome.get("id")) if outcome.get("id") else
                  ("not a case" if outcome.get("case") is False else "failed: " + str(outcome.get("error") or outcome.get("refused"))),
                  flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
