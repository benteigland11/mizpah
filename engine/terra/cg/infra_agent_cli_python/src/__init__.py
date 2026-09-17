"""Source-checkout alias for the declared ``infra-agent-cli-python`` leaf.

Cartograph's local install directory retains a ``cg_`` prefix; the blueprint's
public composition imports the canonical dependency-id namespace.
"""

from cg.cg_infra_agent_cli_python.src.agent_cli import AgentCLI

__all__ = ["AgentCLI"]
