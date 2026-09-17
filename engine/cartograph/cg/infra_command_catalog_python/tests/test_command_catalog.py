import pytest

from src import Catalog


SAMPLE = {
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
            "help": "Check installed widgets.",
            "args": [
                {"name": "widget_id", "nargs": "?", "default": None},
                {"name": "--all", "action": "store_true", "help": "Return every widget"},
                {"name": "--bump", "choices": ["major", "minor", "patch"], "default": "minor"},
            ],
        },
    ],
    "Build": [
        {
            "name": "validate",
            "help": "Run validation.",
            "args": [],
        },
    ],
}


# ---------- construction / validation ----------

def test_rejects_non_dict_sections():
    with pytest.raises(TypeError):
        Catalog(["not", "a", "dict"])


def test_rejects_non_str_section_title():
    with pytest.raises(TypeError):
        Catalog({42: []})


def test_rejects_non_list_commands():
    with pytest.raises(TypeError):
        Catalog({"Section": "not a list"})


def test_rejects_command_without_name():
    with pytest.raises(ValueError):
        Catalog({"Section": [{"help": "no name"}]})


def test_accepts_empty_section():
    c = Catalog({"Section": []})
    assert c.command_names() == []


def test_accepts_empty_catalog():
    c = Catalog({})
    assert c.command_names() == []
    assert c.to_agent_instructions() == ""


# ---------- command_names ----------

def test_command_names_preserves_order():
    c = Catalog(SAMPLE)
    assert c.command_names() == ["install", "status", "validate"]


# ---------- to_agent_instructions ----------

def test_agent_instructions_includes_sections_and_signatures():
    text = Catalog(SAMPLE).to_agent_instructions()
    assert "**Find and use**" in text
    assert "**Build**" in text
    assert "install <widget_id> [--target .] [--version VERSION]" in text
    assert "status [widget_id] [--all] [--bump minor]" in text
    assert "validate" in text
    assert "Install a widget into your project." in text


def test_agent_instructions_indent_customizable():
    text = Catalog(SAMPLE).to_agent_instructions(indent="  ")
    assert "\n  install" in text


def test_agent_instructions_multiline_help_preserved():
    c = Catalog({
        "S": [{"name": "x", "help": "line 1\nline 2"}],
    })
    text = c.to_agent_instructions()
    assert "line 1" in text and "line 2" in text


# ---------- to_markdown ----------

def test_markdown_list_flavor_contains_backticks_and_bullets():
    md = Catalog(SAMPLE).to_markdown(flavor="list")
    assert "### Find and use" in md
    assert "- `install <widget_id>" in md
    assert md.endswith("\n")


def test_markdown_table_flavor_has_headers():
    md = Catalog(SAMPLE).to_markdown(flavor="table")
    assert "| Command | Description |" in md
    assert "|---------|-------------|" in md
    assert "| `install" in md


def test_markdown_table_escapes_pipes():
    c = Catalog({
        "S": [{"name": "x", "help": "foo | bar", "args": []}],
    })
    md = c.to_markdown(flavor="table")
    assert r"foo \| bar" in md


def test_markdown_rejects_unknown_flavor():
    with pytest.raises(ValueError):
        Catalog(SAMPLE).to_markdown(flavor="yaml")


# ---------- to_help_text ----------

def test_help_text_includes_signature_and_sections():
    text = Catalog(SAMPLE).to_help_text("install")
    assert text.startswith("install ")
    assert "Install a widget into your project." in text
    assert "Positional arguments:" in text
    assert "widget_id" in text
    assert "Flags:" in text
    assert "--target" in text


def test_help_text_without_flags_has_no_flags_section():
    text = Catalog(SAMPLE).to_help_text("validate")
    assert "Flags:" not in text
    assert "Positional arguments:" not in text


def test_help_text_unknown_command_raises():
    with pytest.raises(KeyError):
        Catalog(SAMPLE).to_help_text("nope")


# ---------- arg formatting edge cases ----------

def test_store_true_flag_has_no_value_placeholder():
    text = Catalog({
        "S": [{"name": "x", "args": [{"name": "--verbose", "action": "store_true"}]}],
    }).to_agent_instructions()
    assert "[--verbose]" in text
    assert "[--verbose VERBOSE]" not in text


def test_choices_used_as_placeholder():
    text = Catalog({
        "S": [{"name": "x", "args": [{"name": "--bump", "choices": ["major", "minor", "patch"]}]}],
    }).to_agent_instructions()
    assert "[--bump major|minor|patch]" in text


def test_positional_with_default_is_bracketed():
    text = Catalog({
        "S": [{"name": "x", "args": [{"name": "path", "default": "."}]}],
    }).to_agent_instructions()
    assert "[path]" in text


def test_positional_without_default_is_angle_bracketed():
    text = Catalog({
        "S": [{"name": "x", "args": [{"name": "widget_id"}]}],
    }).to_agent_instructions()
    assert "<widget_id>" in text


def test_unknown_arg_keys_ignored():
    c = Catalog({
        "S": [{"name": "x", "help": "h",
               "handler": lambda a: None,
               "extra_key": "whatever",
               "args": [{"name": "--y", "future_field": 1}]}]
    })
    assert "x [--y Y]" in c.to_agent_instructions()
