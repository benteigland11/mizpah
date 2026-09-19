"""Project rules for Python widgets and blueprints in the Mizpah engine.

Runs on every `cartograph validate` / `cartograph checkin` from this directory.
Prints {"blocks": [...], "warnings": [...]}; blocks cannot be overridden.
"""
import json
import os
import re
import sys

# Provider constants belong in examples/, config, or the engine's providers.json — never in src/.
# A widget that bakes them in cannot be pointed at another provider without an edit, and the
# next project would copy the constant instead of the profile.
VENDOR_HOST_PATTERN = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*(?:openai\.com|chatgpt\.com|x\.ai|grok\.com|anthropic\.com|"
    r"googleapis\.com|github\.com|githubcopilot\.com)\b",
    re.IGNORECASE,
)
CLIENT_ID_PATTERN = re.compile(r"""["'](?:app_[A-Za-z0-9]{16,}|Iv1\.[0-9a-f]{16}|[0-9]{10,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com)["']""")
SECRET_PATTERN = re.compile(r"""["'](?:sk-[A-Za-z0-9_-]{16,}|xai-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{16,})["']""")


def _source_files(widget_path, folder):
    root = os.path.join(widget_path, folder)
    for directory, _, files in os.walk(root):
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(directory, name)


def _scan(path, pattern):
    with open(path, encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            match = pattern.search(line)
            if match:
                yield number, match.group(0)


def validate(widget_path):
    blocks = []
    warnings = []
    for path in _source_files(widget_path, "src"):
        rel = os.path.relpath(path, widget_path)
        for number, found in _scan(path, VENDOR_HOST_PATTERN):
            blocks.append(f"{rel}:{number}: vendor endpoint literal {found!r} in src/ — take it as a parameter "
                          "or a profile; keep the constant in examples/ or the engine's providers.json")
        for number, found in _scan(path, CLIENT_ID_PATTERN):
            blocks.append(f"{rel}:{number}: OAuth client id literal {found} in src/ — it is provider data, not code")
    for folder in ("src", "tests", "examples"):
        for path in _source_files(widget_path, folder):
            rel = os.path.relpath(path, widget_path)
            for number, found in _scan(path, SECRET_PATTERN):
                blocks.append(f"{rel}:{number}: credential-shaped literal {found} — never commit a real key; use a fake like 'sk-test'")
    return {"blocks": blocks, "warnings": warnings}


if __name__ == "__main__":
    print(json.dumps(validate(sys.argv[1])))
