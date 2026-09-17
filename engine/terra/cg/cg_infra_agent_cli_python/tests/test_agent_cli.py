import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent_cli import AgentCLI, out, err, ok, fail


def _make_cli():
    """Build a minimal CLI for testing."""
    def cmd_greet(args):
        return {"message": f"hello {args.name}"}

    def cmd_add(args):
        return {"result": args.a + args.b}

    def cmd_cloud_push(args):
        return {"pushed": args.target}

    def cmd_cloud_pull(args):
        return {"pulled": True}

    cli = AgentCLI(prog="test-tool", description="test", version="0.1.0")
    cli.add_commands("Basic", [
        {
            "name": "greet",
            "help": "Say hello",
            "handler": cmd_greet,
            "args": [{"name": "name", "help": "Who to greet"}],
        },
        {
            "name": "add",
            "help": "Add two numbers",
            "handler": cmd_add,
            "args": [
                {"name": "a", "type": int},
                {"name": "b", "type": int},
            ],
        },
    ])
    cli.add_commands("Cloud", [
        {
            "name": "cloud push",
            "help": "Push to cloud",
            "handler": cmd_cloud_push,
            "args": [{"name": "target"}],
        },
        {
            "name": "cloud pull",
            "help": "Pull from cloud",
            "handler": cmd_cloud_pull,
            "args": [],
        },
    ])
    return cli


class TestHelpers:
    def test_ok(self):
        result = ok("done", count=3)
        assert result == {"status": "ok", "message": "done", "count": 3}

    def test_ok_default(self):
        result = ok()
        assert result == {"status": "ok", "message": "success"}

    def test_fail(self):
        result = fail("broke", code=42)
        assert result == {"status": "error", "message": "broke", "code": 42}

    def test_out(self, capsys):
        out({"key": "value"})
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == {"key": "value"}

    def test_err_exits(self):
        try:
            err({"error": "bad"}, code=2)
        except SystemExit as e:
            assert e.code == 2


class TestParserBuilding:
    def test_build_parser(self):
        cli = _make_cli()
        parser = cli.build_parser()
        args = parser.parse_args(["greet", "world"])
        assert args.name == "world"
        assert args.command == "greet"

    def test_positional_types(self):
        cli = _make_cli()
        parser = cli.build_parser()
        args = parser.parse_args(["add", "3", "4"])
        assert args.a == 3
        assert args.b == 4

    def test_nested_subcommand(self):
        cli = _make_cli()
        parser = cli.build_parser()
        args = parser.parse_args(["cloud", "push", "mywidget"])
        assert args.target == "mywidget"

    def test_nested_no_args(self):
        cli = _make_cli()
        parser = cli.build_parser()
        args = parser.parse_args(["cloud", "pull"])
        assert hasattr(args, "func")


class TestOptionalArgs:
    def test_flag_arg(self):
        def cmd_verbose(args):
            return {"verbose": args.verbose}

        cli = AgentCLI(prog="t", version="0.1.0")
        cli.add_commands("Test", [{
            "name": "run",
            "help": "Run it",
            "handler": cmd_verbose,
            "args": [
                {"name": "--verbose", "action": "store_true", "default": False},
            ],
        }])
        parser = cli.build_parser()
        args = parser.parse_args(["run", "--verbose"])
        assert args.verbose is True

    def test_default_value(self):
        cli = AgentCLI(prog="t", version="0.1.0")
        cli.add_commands("Test", [{
            "name": "run",
            "help": "Run",
            "handler": lambda a: {},
            "args": [
                {"name": "--count", "type": int, "default": 10},
            ],
        }])
        parser = cli.build_parser()
        args = parser.parse_args(["run"])
        assert args.count == 10

    def test_choices(self):
        cli = AgentCLI(prog="t", version="0.1.0")
        cli.add_commands("Test", [{
            "name": "run",
            "help": "Run",
            "handler": lambda a: {},
            "args": [
                {"name": "--mode", "choices": ["fast", "slow"], "default": "fast"},
            ],
        }])
        parser = cli.build_parser()
        args = parser.parse_args(["run", "--mode", "slow"])
        assert args.mode == "slow"

    def test_dest_mapping(self):
        cli = AgentCLI(prog="t", version="0.1.0")
        cli.add_commands("Test", [{
            "name": "run",
            "help": "Run",
            "handler": lambda a: {},
            "args": [
                {"name": "--dry-run", "action": "store_true", "dest": "dry_run", "default": False},
            ],
        }])
        parser = cli.build_parser()
        args = parser.parse_args(["run", "--dry-run"])
        assert args.dry_run is True


class TestGroupedHelp:
    def test_contains_groups(self):
        cli = _make_cli()
        help_text = cli.grouped_help()
        assert "Basic:" in help_text
        assert "Cloud:" in help_text

    def test_contains_commands(self):
        cli = _make_cli()
        help_text = cli.grouped_help()
        assert "greet" in help_text
        assert "Say hello" in help_text
        assert "cloud push" in help_text

    def test_contains_prog(self):
        cli = _make_cli()
        help_text = cli.grouped_help()
        assert "test-tool" in help_text


class TestRun:
    def test_handler_return_printed(self, capsys):
        def cmd_ping(args):
            return {"pong": True}

        cli = AgentCLI(prog="t", version="0.1.0")
        cli.add_commands("Test", [{
            "name": "ping",
            "help": "Ping",
            "handler": cmd_ping,
            "args": [],
        }])

        try:
            cli.run(["ping"])
        except SystemExit:
            pass
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == {"pong": True}

    def test_handler_none_no_output(self, capsys):
        def cmd_quiet(args):
            pass

        cli = AgentCLI(prog="t", version="0.1.0")
        cli.add_commands("Test", [{
            "name": "quiet",
            "help": "Quiet",
            "handler": cmd_quiet,
            "args": [],
        }])

        try:
            cli.run(["quiet"])
        except SystemExit:
            pass
        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_help_exits(self):
        cli = _make_cli()
        try:
            cli.run(["--help"])
        except SystemExit as e:
            assert e.code == 0

    def test_no_command_shows_help(self, capsys):
        cli = _make_cli()
        try:
            cli.run([])
        except SystemExit as e:
            assert e.code == 0
        captured = capsys.readouterr()
        assert "Basic:" in captured.out


class TestDeepNesting:
    def test_three_level_nesting(self):
        def cmd_leaf(args):
            return {"id": args.id}

        cli = AgentCLI(prog="t", version="0.1.0")
        cli.add_commands("Cloud", [
            {
                "name": "cloud proposals list",
                "help": "List proposals",
                "handler": lambda a: {"items": []},
                "args": [],
            },
            {
                "name": "cloud proposals view",
                "help": "View a proposal",
                "handler": cmd_leaf,
                "args": [{"name": "id"}],
            },
            {
                "name": "cloud push",
                "help": "Push to cloud",
                "handler": lambda a: {"pushed": True},
                "args": [],
            },
        ])
        parser = cli.build_parser()

        # Three-level command works
        args = parser.parse_args(["cloud", "proposals", "view", "42"])
        assert args.id == "42"
        assert hasattr(args, "func")

        # Two-level sibling still works
        args2 = parser.parse_args(["cloud", "push"])
        assert hasattr(args2, "func")

    def test_four_level_nesting(self):
        cli = AgentCLI(prog="t", version="0.1.0")
        cli.add_commands("Deep", [
            {
                "name": "a b c d",
                "help": "Four levels deep",
                "handler": lambda a: {"deep": True},
                "args": [{"name": "val"}],
            },
        ])
        parser = cli.build_parser()
        args = parser.parse_args(["a", "b", "c", "d", "hello"])
        assert args.val == "hello"

    def test_parent_shows_help(self, capsys):
        cli = AgentCLI(prog="t", version="0.1.0")
        cli.add_commands("Cloud", [
            {
                "name": "cloud proposals list",
                "help": "List proposals",
                "handler": lambda a: {},
                "args": [],
            },
        ])
        parser = cli.build_parser()
        # "cloud proposals" with no subcommand should print help
        args = parser.parse_args(["cloud", "proposals"])
        func = getattr(args, "func", None)
        assert func is not None  # should have the help-printing default


class TestMultipleGroups:
    def test_three_groups(self):
        cli = AgentCLI(prog="t", version="0.1.0")
        cli.add_commands("A", [{"name": "a1", "help": "first", "handler": lambda a: {}, "args": []}])
        cli.add_commands("B", [{"name": "b1", "help": "second", "handler": lambda a: {}, "args": []}])
        cli.add_commands("C", [{"name": "c1", "help": "third", "handler": lambda a: {}, "args": []}])
        help_text = cli.grouped_help()
        assert "A:" in help_text
        assert "B:" in help_text
        assert "C:" in help_text
