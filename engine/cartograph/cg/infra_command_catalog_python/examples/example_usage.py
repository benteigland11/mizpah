"""Demonstrate Catalog rendering over a small sample command surface."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.command_catalog import Catalog

sample = {
    "Find and use": [
        {
            "name": "install",
            "help": "Install a widget into your project.",
            "args": [
                {"name": "widget_id"},
                {"name": "--target", "default": ".", "help": "Project root"},
                {"name": "--version", "default": None},
            ],
        },
        {
            "name": "status",
            "help": "Check installed widgets — omit widget_id to scan all.",
            "args": [
                {"name": "widget_id", "nargs": "?", "default": None},
                {"name": "--page", "default": 1, "type": int},
                {"name": "--all", "action": "store_true"},
            ],
        },
    ],
    "Build": [
        {
            "name": "validate",
            "help": "Run validation on a widget path.",
            "args": [{"name": "path", "nargs": "?", "default": "."}],
        },
    ],
}

c = Catalog(sample)

print("=" * 60)
print("AGENT INSTRUCTIONS")
print("=" * 60)
print(c.to_agent_instructions())

print()
print("=" * 60)
print("MARKDOWN (list)")
print("=" * 60)
print(c.to_markdown(flavor="list"))

print("=" * 60)
print("MARKDOWN (table)")
print("=" * 60)
print(c.to_markdown(flavor="table"))

print("=" * 60)
print("HELP TEXT (install)")
print("=" * 60)
print(c.to_help_text("install"))
