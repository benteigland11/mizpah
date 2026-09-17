"""Command catalog + presentation.

Accepts the declarative command shape used by argparse-style CLI frameworks:

    {
        "name": "install",
        "help": "Install a widget into your project.",
        "args": [
            {"name": "widget_id"},
            {"name": "--target", "default": ".", "help": "Project root"},
            {"name": "--version", "default": None},
            {"name": "--force", "action": "store_true"},
        ],
    }

Emits three presentations of the same catalog:
  * to_agent_instructions() — plain-text block for AGENTS.md-style configs
  * to_markdown()            — tables or bullet lists for READMEs
  * to_help_text(name)       — rich per-command help

The catalog is intentionally schema-light: any dict with name + help is a
command, and any dict with name inside args is a flag. Unknown keys are
ignored so callers can enrich the shape without breaking this widget.
"""

from typing import Iterable


class Catalog:
    """Hold sectioned command definitions and render them."""

    def __init__(self, sections: dict[str, list[dict]]):
        if not isinstance(sections, dict):
            raise TypeError("sections must be a dict of section_title -> list[command]")
        for title, cmds in sections.items():
            if not isinstance(title, str):
                raise TypeError("section titles must be str")
            if not isinstance(cmds, list):
                raise TypeError(f"commands for section {title!r} must be a list")
            for cmd in cmds:
                if not isinstance(cmd, dict) or "name" not in cmd:
                    raise ValueError(f"command in section {title!r} missing 'name'")
        self.sections = sections

    @staticmethod
    def _format_signature(cmd: dict) -> str:
        parts = [cmd["name"]]
        for arg in cmd.get("args", []):
            parts.append(_format_arg(arg))
        return " ".join(parts)

    @staticmethod
    def _iter_commands(sections: dict[str, list[dict]]) -> Iterable[tuple[str, dict]]:
        for title, cmds in sections.items():
            for cmd in cmds:
                yield title, cmd

    def to_agent_instructions(self, indent: str = "    ") -> str:
        """Plain-text block grouped by section. Signature on one line, help on the next."""
        out: list[str] = []
        first = True
        for title, cmds in self.sections.items():
            if not first:
                out.append("")
            first = False
            out.append(f"**{title}**")
            out.append("")
            for cmd in cmds:
                out.append(f"{indent}{self._format_signature(cmd)}")
                help_text = cmd.get("help", "")
                if help_text:
                    for line in help_text.splitlines():
                        out.append(f"{indent}  {line}")
                out.append("")
        while out and out[-1] == "":
            out.pop()
        return "\n".join(out)

    def to_markdown(self, flavor: str = "list") -> str:
        """Markdown. flavor='list' for bullet lists, 'table' for a pipe table."""
        if flavor not in ("list", "table"):
            raise ValueError("flavor must be 'list' or 'table'")
        out: list[str] = []
        for title, cmds in self.sections.items():
            out.append(f"### {title}")
            out.append("")
            if flavor == "table":
                out.append("| Command | Description |")
                out.append("|---------|-------------|")
                for cmd in cmds:
                    sig = self._format_signature(cmd).replace("|", r"\|")
                    help_one_line = (cmd.get("help") or "").replace("\n", " ").replace("|", r"\|")
                    out.append(f"| `{sig}` | {help_one_line} |")
            else:
                for cmd in cmds:
                    sig = self._format_signature(cmd)
                    help_first = (cmd.get("help") or "").splitlines()
                    help_first = help_first[0] if help_first else ""
                    out.append(f"- `{sig}` — {help_first}")
            out.append("")
        return "\n".join(out).rstrip() + "\n"

    def to_help_text(self, command_name: str) -> str:
        """Rich per-command help: signature + help + positionals + flags."""
        for _, cmd in self._iter_commands(self.sections):
            if cmd["name"] == command_name:
                lines = [self._format_signature(cmd)]
                if cmd.get("help"):
                    lines.append("")
                    lines.append(cmd["help"])
                positionals = [a for a in cmd.get("args", []) if not a.get("name", "").startswith("--")]
                flags = [a for a in cmd.get("args", []) if a.get("name", "").startswith("--")]
                if positionals:
                    lines.append("")
                    lines.append("Positional arguments:")
                    for arg in positionals:
                        lines.append(f"  {arg['name']}  {arg.get('help', '')}".rstrip())
                if flags:
                    lines.append("")
                    lines.append("Flags:")
                    for arg in flags:
                        lines.append(
                            f"  {_format_arg(arg, brackets=False)}  {arg.get('help', '')}".rstrip()
                        )
                return "\n".join(lines)
        raise KeyError(f"Unknown command: {command_name}")

    def command_names(self) -> list[str]:
        return [cmd["name"] for _, cmd in self._iter_commands(self.sections)]


def _format_arg(arg: dict, brackets: bool = True) -> str:
    """Render one arg in signature form. Positional <req> vs [opt], flags bracketed."""
    name = arg["name"]
    action = arg.get("action")
    is_flag = name.startswith("--")

    if is_flag:
        if action == "store_true":
            token = name
        else:
            default = arg.get("default")
            placeholder = _placeholder_for(arg)
            if default not in (None, ""):
                token = f"{name} {default}"
            else:
                token = f"{name} {placeholder}"
        return f"[{token}]" if brackets else token

    nargs = arg.get("nargs")
    if nargs == "?" or arg.get("default") is not None:
        return f"[{name}]"
    return f"<{name}>"


def _placeholder_for(arg: dict) -> str:
    choices = arg.get("choices")
    if choices:
        return "|".join(str(c) for c in choices)
    dest = arg.get("dest") or arg["name"].lstrip("-").replace("-", "_")
    return dest.upper() if dest else "X"
