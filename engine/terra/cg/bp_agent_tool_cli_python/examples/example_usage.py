"""
Example: agent-tool-cli blueprint (sealed API only).

Runs cleanly with no stdin, network, or external services.
"""
from __future__ import annotations

import sys

from src.agent_tool_cli import AgentToolCLI, emit, format_json, tool_error, tool_success


def cmd_search(args):
    results = [
        {"id": "item-1", "name": "Rate Limiter", "score": 0.95},
        {"id": "item-2", "name": "Retry Backoff", "score": 0.82},
    ]
    limit = args.limit
    if limit is not None:
        results = results[: int(limit)]
    return tool_success(
        {"query": args.query, "results": results, "count": len(results)},
        meta={"surface": "demo.search"},
    )


def cmd_fail(args):
    return tool_error("not found", code="NOT_FOUND")


cli = AgentToolCLI(
    prog="demo-tool",
    description="Demo agent-facing CLI (blueprint composition)",
    version="0.1.0",
)
cli.add_commands(
    "Find",
    [
        {
            "name": "search",
            "help": "Search items",
            "handler": cmd_search,
            "args": [
                {"name": "query", "help": "Search query"},
                {
                    "name": "--limit",
                    "type": int,
                    "default": None,
                    "help": "Max results",
                },
            ],
        },
        {
            "name": "missing",
            "help": "Always errors (envelope demo)",
            "handler": cmd_fail,
            "args": [],
        },
    ],
)

# Grouped help (human/agent readable)
sys.stdout.write(cli.grouped_help().splitlines()[0] + "\n")

parser = cli.build_parser()
args = parser.parse_args(["search", "rate limiter", "--limit", "1"])
result = args.func(args)
emit(result)

args2 = parser.parse_args(["missing"])
err = args2.func(args2)
# Print error envelope via sealed format_json (no leaf import)
sys.stdout.write(format_json(err) + "\n")
