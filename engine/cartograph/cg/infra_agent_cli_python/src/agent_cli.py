"""
Agent CLI - a declarative CLI framework for LLM/agent-driven tools.

Design principles:
  - All output is JSON (structured, parseable by agents)
  - No interactive prompts (agents can't answer y/n)
  - Grouped help text (readable by humans and agents)
  - Commands are plain functions that receive parsed args
  - Declarative: define commands as dicts, get argparse wiring for free

Usage:

    from agent_cli import AgentCLI, out, err

    def cmd_greet(args):
        return {"message": f"hello {args.name}"}

    cli = AgentCLI(
        prog="mytool",
        description="My agent-friendly tool",
        version="1.0.0",
    )
    cli.add_commands("Basics", [
        {
            "name": "greet",
            "help": "Say hello",
            "handler": cmd_greet,
            "args": [
                {"name": "name", "help": "Who to greet"},
            ],
        },
    ])
    cli.run()
"""

import argparse
import json
import sys


def out(result: dict) -> None:
    """Write a JSON result to stdout."""
    sys.stdout.write(json.dumps(result, indent=2) + "\n")


def err(result: dict, code: int = 1) -> None:
    """Write a JSON error to stdout and exit."""
    out(result)
    sys.exit(code)


def ok(message: str = "success", **extra) -> dict:
    """Build a success result dict."""
    return {"status": "ok", "message": message, **extra}


def fail(message: str, **extra) -> dict:
    """Build an error result dict."""
    return {"status": "error", "message": message, **extra}


class AgentCLI:
    """Declarative CLI builder that produces agent-friendly JSON output."""

    def __init__(self, prog: str, description: str = "", version: str = "",
                 colors: dict | None = None):
        self.prog = prog
        self.description = description
        self.version = version
        self._groups: list[tuple[str, list[dict]]] = []
        self.colors = colors or {}
        # colors dict:
        #   heading:  ANSI code for all group headers (consistent)
        #   groups:   list of ANSI codes, one per group (cycles if fewer than groups)

    def add_commands(self, group_name: str, commands: list[dict]) -> None:
        """Register a group of commands.

        Each command dict has:
            name:     str           - subcommand name (use "parent child" for nested)
            help:     str           - one-line description
            handler:  callable      - function(args) -> dict or None
            args:     list[dict]    - argument specs (see _add_arg)
        """
        self._groups.append((group_name, commands))

    def build_parser(self) -> argparse.ArgumentParser:
        """Build the argparse parser from declared command groups."""
        parser = argparse.ArgumentParser(
            prog=self.prog,
            description=self.description,
            usage=argparse.SUPPRESS,
            add_help=False,
        )
        parser.add_argument("-h", "--help", action="store_true", default=False)
        if self.version:
            parser.add_argument(
                "-v", "--version", action="version",
                version=f"{self.prog} {self.version}",
            )

        sub = parser.add_subparsers(dest="command", metavar="<command>")
        # Maps a tuple of parent parts to (parser, subparsers) for nested commands.
        # e.g. ("cloud",) -> (cloud_parser, cloud_subparsers)
        #      ("cloud", "proposals") -> (proposals_parser, proposals_subparsers)
        self._nested_parsers = {}

        for _group_name, commands in self._groups:
            for cmd in commands:
                name = cmd["name"]
                parts = name.split()
                handler = cmd.get("handler")

                if len(parts) == 1:
                    p = sub.add_parser(
                        name,
                        help=cmd.get("help", ""),
                        description=cmd.get("description") or cmd.get("help", ""),
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                    )
                else:
                    # Walk the parent chain, creating subparsers at each level
                    current_sub = sub
                    for depth in range(len(parts) - 1):
                        key = tuple(parts[: depth + 1])
                        if key not in self._nested_parsers:
                            parent_name = parts[depth]
                            dest = "_".join(key) + "_command"
                            parent_parser = current_sub.add_parser(
                                parent_name, help=f"{parent_name} operations",
                            )
                            parent_sub = parent_parser.add_subparsers(dest=dest)
                            self._nested_parsers[key] = (parent_parser, parent_sub)
                        _, current_sub = self._nested_parsers[key]
                    p = current_sub.add_parser(
                        parts[-1],
                        help=cmd.get("help", ""),
                        description=cmd.get("description") or cmd.get("help", ""),
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                    )

                for arg_spec in cmd.get("args", []):
                    self._add_arg(p, arg_spec)

                if handler:
                    p.set_defaults(func=handler)

        for key, (parent_parser, _) in self._nested_parsers.items():
            parent_parser.set_defaults(func=lambda args, pp=parent_parser: pp.print_help())

        return parser

    def grouped_help(self) -> str:
        """Render grouped help text with optional color.

        Colors are configured via the `colors` dict passed to __init__:
            heading:  ANSI code for all group headers (same color)
            groups:   list of ANSI codes, one per group for command names
        Automatically disabled when stdout is not a tty.
        """
        use_color = self.colors and sys.stdout.isatty()
        r = "\033[0m" if use_color else ""
        h = self.colors.get("heading", "") if use_color else ""
        group_colors = self.colors.get("groups", []) if use_color else []

        lines = ["", f"usage: {self.prog} <command> [options]", ""]
        for i, (group_name, commands) in enumerate(self._groups):
            c = group_colors[i % len(group_colors)] if group_colors else ""
            lines.append(f"  {h}{group_name}:{r}")
            for cmd in commands:
                name = cmd["name"]
                desc = cmd.get("help", "")
                lines.append(f"    {c}{name:<16s}{r} {desc}")
            lines.append("")
        hint_c = group_colors[0] if group_colors else ""
        lines.append(f"  Run '{hint_c}{self.prog} <command> -h{r}' for command-specific help.")
        lines.append("")
        return "\n".join(lines)

    def run(self, argv: list[str] = None) -> None:
        """Parse args and dispatch to the handler.

        If the handler returns a dict, it is printed as JSON.
        Handlers can also call out()/err() directly for more control.
        """
        parser = self.build_parser()
        args = parser.parse_args(argv)

        if args.help or not args.command:
            sys.stdout.write(self.grouped_help())
            sys.exit(0)

        handler = getattr(args, "func", None)
        if not handler:
            sys.stdout.write(self.grouped_help())
            sys.exit(0)

        result = handler(args)
        if isinstance(result, dict):
            out(result)

    @staticmethod
    def _add_arg(parser: argparse.ArgumentParser, spec: dict) -> None:
        """Add an argument to a parser from a spec dict.

        Spec keys:
            name:       str   - positional arg name or --flag name
            help:       str   - help text
            required:   bool  - for optional args
            default:    any   - default value
            type:       type  - int, float, str, etc.
            choices:    list  - valid values
            action:     str   - "store_true", "store_false", etc.
            nargs:      str   - "?", "*", "+", etc.
            dest:       str   - attribute name on the parsed args
        """
        name = spec["name"]
        kwargs = {}
        for key in ("help", "required", "default", "type", "choices", "action", "nargs", "const", "dest"):
            if key in spec:
                kwargs[key] = spec[key]

        if name.startswith("-"):
            parser.add_argument(name, **kwargs)
        else:
            kwargs.pop("required", None)
            parser.add_argument(name, **kwargs)
