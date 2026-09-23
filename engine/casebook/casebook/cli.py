"""Command-line interface: JSON out, one verb per command."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from casebook import cases, ops


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        payload = args.handler(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("ok", True) else 1


def _search(args: argparse.Namespace) -> dict[str, Any]:
    return ops.search(" ".join(args.words), args.limit)


def _show(args: argparse.Namespace) -> dict[str, Any]:
    return ops.show(args.id)


def _list(args: argparse.Namespace) -> dict[str, Any]:
    return ops.list_cases()


def _validate(args: argparse.Namespace) -> dict[str, Any]:
    return ops.validate(args.id)


def _add(args: argparse.Namespace) -> dict[str, Any]:
    if args.file:
        data = json.loads(sys.stdin.read() if args.file == "-" else open(args.file).read())
        problem, diagnosis, fix = data.get("problem", ""), data.get("diagnosis", ""), data.get("fix", "")
        touches = data.get("touches") or []
        raw = data.get("instance") or {}
        item = cases.instance(str(raw.get("where") or ""), str(raw.get("symptom") or ""), str(raw.get("change") or ""),
                              raw.get("cost"), str(raw.get("source") or ""))
        into = args.into or data.get("into")
    else:
        problem, diagnosis, fix = args.problem or "", args.diagnosis or "", args.fix or ""
        touches = [t.strip() for t in (args.touches or "").split(",") if t.strip()]
        item = cases.instance(args.where or "", args.symptom or "", args.change or "", args.cost, args.source or "")
        into = args.into
    return ops.add(problem, diagnosis, fix, item, touches=touches, into=into, new=args.new)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="casebook", description="Cases: a problem as it presented, what it was, what fixed it.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="cases whose problem looks like yours: say what you see (the error, the red reading, the surprise)")
    p.add_argument("words", nargs="*")
    p.add_argument("--limit", type=int)
    p.set_defaults(handler=_search)

    p = sub.add_parser("show", help="one case with every instance of it")
    p.add_argument("id")
    p.set_defaults(handler=_show)

    p = sub.add_parser("list", help="every case, by its problem")
    p.set_defaults(handler=_list)

    p = sub.add_parser("validate", help="check the store (or one case) against what a case must be")
    p.add_argument("id", nargs="?")
    p.set_defaults(handler=_validate)

    p = sub.add_parser("add", help="file a case, or one more instance of an existing case with --into")
    p.add_argument("--problem", help="the symptom as it presented, in general words")
    p.add_argument("--diagnosis", help="what it turned out to be")
    p.add_argument("--fix", help="what got it unstuck")
    p.add_argument("--where", help="the episode: which project and task")
    p.add_argument("--symptom", help="the episode's raw symptom: the error or red reading as it showed")
    p.add_argument("--change", help="the episode's change that made it pass")
    p.add_argument("--cost", type=int, help="turns spent stuck")
    p.add_argument("--source", help="where in the trace")
    p.add_argument("--touches", help="comma-separated tools, formats or widgets involved")
    p.add_argument("--into", help="an existing case this episode is another instance of")
    p.add_argument("--new", action="store_true", help="file it even though a case with a similar diagnosis exists")
    p.add_argument("--file", help="read the case as JSON from a file, or - for stdin")
    p.set_defaults(handler=_add)
    return parser
