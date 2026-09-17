"""Tests hit the blueprint public API only (not leaf modules)."""

from __future__ import annotations

from types import SimpleNamespace

from src.agent_tool_cli import (
    AgentToolCLI,
    format_json,
    is_success,
    tool_error,
    tool_success,
    wrap_handler,
)


def test_tool_success_envelope():
    env = tool_success({"count": 2}, meta={"surface": "demo"})
    assert env["status"] == "success"
    assert env["data"]["count"] == 2
    assert env["meta"]["surface"] == "demo"
    assert is_success(env)
    assert "success" in format_json(env)


def test_tool_error_envelope():
    env = tool_error("nope", code="E_FAIL")
    assert env["status"] == "error"
    assert env["error"]["message"] == "nope"
    assert env["error"]["code"] == "E_FAIL"
    assert not is_success(env)


def test_wrap_handler_plain_dict():
    def handler(args):
        return {"item_id": args.item_id}

    wrapped = wrap_handler(handler)
    out = wrapped(SimpleNamespace(item_id="x1"))
    assert out["status"] == "success"
    assert out["data"]["item_id"] == "x1"


def test_wrap_handler_passthrough_success():
    def handler(args):
        return tool_success({"ok": True})

    out = wrap_handler(handler)(SimpleNamespace())
    assert out["status"] == "success"
    assert out["data"]["ok"] is True


def test_wrap_handler_normalizes_leaf_ok():
    def handler(args):
        return {"status": "ok", "message": "found", "count": 1}

    out = wrap_handler(handler)(SimpleNamespace())
    assert out["status"] == "success"
    assert out["data"]["count"] == 1


def test_wrap_handler_exception():
    def handler(args):
        raise ValueError("bad input")

    out = wrap_handler(handler)(SimpleNamespace())
    assert out["status"] == "error"
    assert "bad input" in out["error"]["message"]
    assert out["error"]["code"] == "ValueError"


def test_agent_tool_cli_dispatch_envelope():
    def cmd_inspect(args):
        return {"id": args.item_id, "version": "1.0.0"}

    cli = AgentToolCLI(prog="demo-tool", description="demo", version="0.1.0")
    cli.add_commands(
        "Items",
        [
            {
                "name": "inspect",
                "help": "Show one item",
                "handler": cmd_inspect,
                "args": [{"name": "item_id", "help": "Item id"}],
            }
        ],
    )
    parser = cli.build_parser()
    args = parser.parse_args(["inspect", "widget-1"])
    result = args.func(args)
    assert result["status"] == "success"
    assert result["data"]["id"] == "widget-1"
    help_text = cli.grouped_help()
    assert "demo-tool" in help_text or "Items" in help_text or "inspect" in help_text


def test_emit_and_wrap_scalar(capsys):
    from src.agent_tool_cli import emit

    code = emit(tool_success({"n": 1}))
    assert code == 0
    captured = capsys.readouterr()
    assert "success" in captured.out
    assert '"n"' in captured.out or "n" in captured.out

    code = emit(tool_error("boom"))
    assert code == 1

    def handler(args):
        return 42

    out = wrap_handler(handler)(SimpleNamespace())
    assert out["status"] == "success"
    assert out["data"]["value"] == 42


def test_wrap_handler_leaf_error_shape():
    def handler(args):
        return {"status": "error", "message": "denied", "code": "E_DENY"}

    out = wrap_handler(handler)(SimpleNamespace())
    assert out["status"] == "error"
    assert out["error"]["message"] == "denied"


def test_run_prints_envelope(capsys):
    def cmd_ping(args):
        return {"pong": True}

    cli = AgentToolCLI(prog="demo-tool", version="0.1.0")
    cli.add_commands(
        "Core",
        [
            {
                "name": "ping",
                "help": "Ping",
                "handler": cmd_ping,
                "args": [],
            }
        ],
    )
    cli.run(["ping"])
    captured = capsys.readouterr()
    assert "success" in captured.out
    assert "pong" in captured.out
