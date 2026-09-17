"""Sealed agent-tool CLI feature (blueprint composition).

Composes:
  - universal-agent-response-python → envelope {status, data|error, meta?}
  - infra-agent-cli-python        → declarative CLI, JSON stdout, no prompts

Public API is this module only. Consumers must not import leaves directly
for the sealed surface — install this blueprint.
"""

from __future__ import annotations

from typing import Any, Callable

from cg.infra_agent_cli_python.src.agent_cli import AgentCLI
from cg.infra_agent_cli_python.src.agent_cli import out as _leaf_out
from cg.universal_agent_response_python.src.agent_response import AgentResponse


def tool_success(
    data: Any,
    *,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a success envelope (machine-first)."""
    return AgentResponse.success(data, meta=meta)


def tool_error(
    message: str,
    *,
    code: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an error envelope (machine-first)."""
    return AgentResponse.error(message, code=code, meta=meta)


def emit(response: dict[str, Any]) -> int:
    """Print one JSON envelope to stdout. Return 0 on success, 1 on error."""
    _leaf_out(response)
    status = response.get("status")
    return 0 if status in ("success", "ok") else 1


def format_json(response: dict[str, Any]) -> str:
    """Serialize an envelope to a JSON string."""
    return AgentResponse.format_json(response)


def is_success(response: dict[str, Any]) -> bool:
    """True when envelope status is success/ok."""
    return response.get("status") in ("success", "ok")


def wrap_handler(handler: Callable[..., Any]) -> Callable[..., dict[str, Any]]:
    """Adapt a handler so return values become agent-response envelopes.

    - dict with ``status`` in {success, ok, error} → normalized envelope
    - any other dict → ``tool_success(dict)``
    - other values → ``tool_success({"value": ...})``
    - raised Exception → ``tool_error(str(exc), code=type name)``
    """

    def _wrapped(args: Any) -> dict[str, Any]:
        try:
            result = handler(args)
        except Exception as exc:  # noqa: BLE001 — edge of agent tool surface
            return tool_error(str(exc), code=type(exc).__name__)
        if isinstance(result, dict) and result.get("status") in (
            "success",
            "ok",
            "error",
        ):
            if result.get("status") == "ok" and "data" not in result:
                rest = {
                    k: v
                    for k, v in result.items()
                    if k not in ("status", "message")
                }
                meta = (
                    {"message": result["message"]}
                    if "message" in result
                    else None
                )
                payload = rest if rest else {"message": result.get("message")}
                return tool_success(payload, meta=meta)
            if result.get("status") == "error" and "error" not in result:
                return tool_error(
                    str(result.get("message") or "error"),
                    code=result.get("code")
                    if isinstance(result.get("code"), str)
                    else None,
                )
            return result
        if isinstance(result, dict):
            return tool_success(result)
        return tool_success({"value": result})

    return _wrapped


class AgentToolCLI:
    """Declarative agent CLI that always emits agent-response envelopes.

    Same group/command shape as infra AgentCLI; handlers may return plain
    dicts and are wrapped into {status, data|error, meta?}.
    """

    def __init__(
        self,
        prog: str,
        description: str = "",
        version: str = "",
        colors: dict[str, Any] | None = None,
    ) -> None:
        self._cli = AgentCLI(
            prog=prog,
            description=description,
            version=version,
            colors=colors,
        )
        self.prog = prog
        self.description = description
        self.version = version

    def add_commands(self, group_name: str, commands: list[dict[str, Any]]) -> None:
        """Register a group of commands; handlers are envelope-wrapped."""
        adapted: list[dict[str, Any]] = []
        for cmd in commands:
            c = dict(cmd)
            handler = c.get("handler")
            if handler is not None:
                c["handler"] = wrap_handler(handler)
            adapted.append(c)
        self._cli.add_commands(group_name, adapted)

    def build_parser(self) -> Any:
        return self._cli.build_parser()

    def grouped_help(self) -> str:
        return self._cli.grouped_help()

    def run(self, argv: list[str] | None = None) -> None:
        """Parse args, dispatch, print JSON envelope (or help text)."""
        import sys

        parser = self.build_parser()
        args = parser.parse_args(argv)

        if getattr(args, "help", False) or not getattr(args, "command", None):
            sys.stdout.write(self.grouped_help())
            sys.exit(0)

        handler = getattr(args, "func", None)
        if not handler:
            sys.stdout.write(self.grouped_help())
            sys.exit(0)

        result = handler(args)
        if isinstance(result, dict):
            code = emit(result)
            if code != 0:
                sys.exit(code)
