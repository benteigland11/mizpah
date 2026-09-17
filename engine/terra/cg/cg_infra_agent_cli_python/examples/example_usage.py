"""
Example usage of Agent CLI.

This file must run and exit cleanly with no user input, no network calls,
and no external services or API keys. Use fake/hardcoded data to demonstrate the API.
The widget's own declared dependencies are fine - the validator installs them first.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.agent_cli import AgentCLI, out, ok, fail


# -- Define command handlers (plain functions, return dicts) --

def cmd_search(args):
    """Search for items matching a query."""
    results = [
        {"id": "widget-1", "name": "Rate Limiter", "score": 0.95},
        {"id": "widget-2", "name": "Retry Backoff", "score": 0.82},
    ]
    if args.limit:
        results = results[:args.limit]
    return ok("found results", count=len(results), results=results)


def cmd_inspect(args):
    """Show details for one item."""
    return ok("found", item={"id": args.item_id, "version": "1.2.0", "status": "active"})


def cmd_remote_push(args):
    """Push an item to a remote registry."""
    return ok(f"pushed {args.item_id}", version="1.2.1")


def cmd_remote_pull(args):
    """Pull the latest version from remote."""
    return ok("pulled", item_id=args.item_id, version="1.3.0")


# -- Build the CLI declaratively --

cli = AgentCLI(
    prog="demo-tool",
    description="A demo agent-friendly CLI",
    version="0.1.0",
)

cli.add_commands("Find items", [
    {
        "name": "search",
        "help": "Search for items",
        "handler": cmd_search,
        "args": [
            {"name": "query", "help": "Search query"},
            {"name": "--limit", "type": int, "default": None, "help": "Max results"},
        ],
    },
    {
        "name": "inspect",
        "help": "Show item details",
        "handler": cmd_inspect,
        "args": [
            {"name": "item_id", "help": "Item to inspect"},
        ],
    },
])

cli.add_commands("Remote", [
    {
        "name": "remote push",
        "help": "Push item to remote",
        "handler": cmd_remote_push,
        "args": [
            {"name": "item_id", "help": "Item to push"},
        ],
    },
    {
        "name": "remote pull",
        "help": "Pull latest from remote",
        "handler": cmd_remote_pull,
        "args": [
            {"name": "item_id", "help": "Item to pull"},
        ],
    },
])

# -- Run with fake args (no stdin needed) --

# Show grouped help
help_text = cli.grouped_help()
for line in help_text.splitlines()[:10]:
    sys.stdout.write(line + "\n")

# Simulate a command dispatch
parser = cli.build_parser()
args = parser.parse_args(["search", "rate limiter", "--limit", "1"])
result = args.func(args)
out(result)
