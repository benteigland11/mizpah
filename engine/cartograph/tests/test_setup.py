"""Tests for the setup command - auto-detection, write, print, workflows."""
import sys
import os

import pytest


@pytest.fixture(autouse=True)
def _clear_agent_env(monkeypatch):
    """Clear agent env vars so tests reflect per-project marker logic only.
    Without this, running the test suite inside an active agent session
    (e.g. Claude Code) would make CLAUDECODE=1 leak into every test."""
    for var in ("CLAUDECODE", "GEMINI_CLI", "AGENT"):
        monkeypatch.delenv(var, raising=False)


def _set_home(monkeypatch, path):
    """Redirect the home directory cross-platform: expanduser('~') reads
    HOME on POSIX but USERPROFILE on Windows."""
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))


def _clean_subprocess_env(home=None):
    """Environment for subprocess tests with agent env vars stripped.

    HOME is pointed at an empty (or caller-supplied) directory so the
    developer's real ~/.claude.json / ~/.codex/config.toml can't leak MCP
    detection into the tests.
    """
    import tempfile
    env = os.environ.copy()
    for var in ("CLAUDECODE", "GEMINI_CLI", "AGENT"):
        env.pop(var, None)
    fake_home = str(home) if home else tempfile.mkdtemp()
    env["HOME"] = fake_home
    env["USERPROFILE"] = fake_home
    # HOME redirection hides user-site installs, so make the checked-out
    # package importable in the subprocess explicitly.
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    env["PYTHONPATH"] = (
        os.path.join(repo, "src") + os.pathsep + repo
        + os.pathsep + env.get("PYTHONPATH", "")
    )
    return env


# ---------------------------------------------------------------------------
# _detect_agent
# ---------------------------------------------------------------------------

def test_detect_claude_from_dotdir(tmp_path):
    """Detect claude from .claude/ directory."""
    from cartograph.cli import _detect_agent
    os.makedirs(tmp_path / ".claude")
    agent, reason = _detect_agent(str(tmp_path))
    assert agent == "claude"
    assert ".claude/" in reason


def test_detect_claude_from_env_var(tmp_path, monkeypatch):
    """CLAUDECODE=1 env var takes precedence over missing per-project markers."""
    from cartograph.cli import _detect_agent
    monkeypatch.setenv("CLAUDECODE", "1")
    agent, reason = _detect_agent(str(tmp_path))
    assert agent == "claude"
    assert "CLAUDECODE" in reason


def test_detect_gemini_from_env_var(tmp_path, monkeypatch):
    """GEMINI_CLI=1 env var is authoritative."""
    from cartograph.cli import _detect_agent
    monkeypatch.setenv("GEMINI_CLI", "1")
    agent, reason = _detect_agent(str(tmp_path))
    assert agent == "gemini"
    assert "GEMINI_CLI" in reason


def test_detect_claude_from_file(tmp_path):
    """Detect claude from CLAUDE.md file."""
    from cartograph.cli import _detect_agent
    (tmp_path / "CLAUDE.md").write_text("# stuff")
    agent, reason = _detect_agent(str(tmp_path))
    assert agent == "claude"
    assert "CLAUDE.md" in reason


def test_detect_cursor(tmp_path):
    """Detect cursor from .cursor/ directory."""
    from cartograph.cli import _detect_agent
    os.makedirs(tmp_path / ".cursor")
    agent, reason = _detect_agent(str(tmp_path))
    assert agent == "cursor"
    assert ".cursor/" in reason


def test_detect_agents_generic(tmp_path):
    """AGENTS.md maps to generic 'agents' (cross-agent convention), not codex.
    Codex does not yet set a subprocess env var (openai/codex#13416) and
    AGENTS.md is used by Codex, Amp, and others - not codex-specific."""
    from cartograph.cli import _detect_agent
    (tmp_path / "AGENTS.md").write_text("# agents")
    agent, reason = _detect_agent(str(tmp_path))
    assert agent == "agents"
    assert "AGENTS.md" in reason


def test_detect_gemini(tmp_path):
    """Detect gemini from GEMINI.md file."""
    from cartograph.cli import _detect_agent
    (tmp_path / "GEMINI.md").write_text("# gemini")
    agent, reason = _detect_agent(str(tmp_path))
    assert agent == "gemini"


def test_detect_nothing(tmp_path):
    """No agent files returns None."""
    from cartograph.cli import _detect_agent
    agent, reason = _detect_agent(str(tmp_path))
    assert agent is None
    assert reason is None


def test_detect_priority_claude_over_cursor(tmp_path):
    """Claude should win over cursor when both present."""
    from cartograph.cli import _detect_agent
    os.makedirs(tmp_path / ".claude")
    os.makedirs(tmp_path / ".cursor")
    agent, _ = _detect_agent(str(tmp_path))
    assert agent == "claude"


# ---------------------------------------------------------------------------
# cmd_setup - integration via subprocess
# ---------------------------------------------------------------------------

def test_setup_print_mode(tmp_path):
    """--print should output instructions without writing any file."""
    import subprocess
    os.makedirs(tmp_path / ".claude")
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "setup", "--print"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
        env=_clean_subprocess_env(),
    )
    assert result.returncode == 0
    assert "## Cartograph" in result.stdout
    # Should NOT have created a file
    assert not (tmp_path / "CLAUDE.md").exists()


def test_setup_writes_to_detected_agent(tmp_path):
    """Bare setup should auto-detect and write."""
    import subprocess
    os.makedirs(tmp_path / ".claude")
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "setup"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
        env=_clean_subprocess_env(),
    )
    assert result.returncode == 0
    assert "Appended to" in result.stdout
    assert "Detected claude" in result.stdout
    content = (tmp_path / "CLAUDE.md").read_text()
    assert "## Cartograph" in content


def test_setup_appends_not_replaces(tmp_path):
    """Setup should append to existing file, not overwrite."""
    import subprocess
    os.makedirs(tmp_path / ".claude")
    existing = "# My Project\n\nSome existing content.\n"
    (tmp_path / "CLAUDE.md").write_text(existing)
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "setup"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
        env=_clean_subprocess_env(),
    )
    assert result.returncode == 0
    content = (tmp_path / "CLAUDE.md").read_text()
    assert content.startswith("# My Project")
    assert "## Cartograph" in content


def test_setup_duplicate_detection(tmp_path):
    """Running setup twice should succeed with 'already exists' message."""
    import subprocess
    os.makedirs(tmp_path / ".claude")
    # First run
    subprocess.run(
        [sys.executable, "-m", "cartograph", "setup"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
        env=_clean_subprocess_env(),
    )
    # Second run
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "setup"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
        env=_clean_subprocess_env(),
    )
    assert result.returncode == 0
    assert "already exists" in result.stdout


def test_setup_custom_file(tmp_path):
    """--file should write to a custom filename."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "setup", "--file", "opencode.md"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
        env=_clean_subprocess_env(),
    )
    assert result.returncode == 0
    assert (tmp_path / "opencode.md").exists()
    content = (tmp_path / "opencode.md").read_text()
    assert "## Cartograph" in content


def test_setup_explicit_agent(tmp_path):
    """--agent should override auto-detection."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "setup", "--agent", "codex"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
        env=_clean_subprocess_env(),
    )
    assert result.returncode == 0
    assert (tmp_path / "AGENTS.md").exists()


def test_setup_no_agent_no_file_shows_help(tmp_path):
    """No agent detected and no flags should show helpful guidance."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "setup"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
        env=_clean_subprocess_env(),
    )
    assert result.returncode == 0
    assert "Could not auto-detect" in result.stdout
    assert "--agent" in result.stdout
    assert "--file" in result.stdout


def test_setup_with_workflow(tmp_path):
    """--workflow should include the workflow section."""
    import subprocess
    os.makedirs(tmp_path / ".claude")
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "setup", "--workflow"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
        env=_clean_subprocess_env(),
    )
    assert result.returncode == 0
    content = (tmp_path / "CLAUDE.md").read_text()
    assert "### Workflow" in content


def test_setup_without_workflow_shows_tip(tmp_path):
    """Without --workflow, output should hint about it."""
    import subprocess
    os.makedirs(tmp_path / ".claude")
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "setup"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
        env=_clean_subprocess_env(),
    )
    assert "Tip: add --workflow" in result.stdout


# ---------------------------------------------------------------------------
# MCP mode - detection and trimmed instructions
# ---------------------------------------------------------------------------

def test_detect_mcp_from_project_mcp_json(tmp_path):
    """A cartograph server in the project .mcp.json is evidence."""
    import json
    from cartograph.cli import _detect_mcp
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"cartograph": {"command": "cartograph-mcp"}}}))
    found, reason = _detect_mcp("claude", str(tmp_path))
    assert found
    assert ".mcp.json" in reason


def test_detect_mcp_nothing(tmp_path, monkeypatch):
    """No MCP config anywhere -> not detected."""
    from cartograph.cli import _detect_mcp
    _set_home(monkeypatch, tmp_path / "home")
    found, reason = _detect_mcp("claude", str(tmp_path))
    assert not found and reason is None


def test_detect_mcp_other_project_does_not_count(tmp_path, monkeypatch):
    """A cartograph server wired into ANOTHER project in ~/.claude.json
    is not evidence for this one."""
    import json
    from cartograph.cli import _detect_mcp
    home = tmp_path / "home"
    home.mkdir()
    _set_home(monkeypatch, home)
    (home / ".claude.json").write_text(json.dumps(
        {"projects": {"/other/project": {"mcpServers": {"cartograph": {}}}}}))
    found, _ = _detect_mcp("claude", str(tmp_path))
    assert not found


def test_detect_mcp_user_scope_counts(tmp_path, monkeypatch):
    """User-scope mcpServers in ~/.claude.json applies to every project."""
    import json
    from cartograph.cli import _detect_mcp
    home = tmp_path / "home"
    home.mkdir()
    _set_home(monkeypatch, home)
    (home / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"plugin:cartograph:cartograph": {}}}))
    found, reason = _detect_mcp("claude", str(tmp_path))
    assert found
    assert ".claude.json" in reason


def test_detect_mcp_codex_toml_header_only(tmp_path, monkeypatch):
    """Codex config matches the [mcp_servers.*cartograph] table header,
    not free text mentioning cartograph."""
    from cartograph.cli import _detect_mcp
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    _set_home(monkeypatch, home)
    cfg = home / ".codex" / "config.toml"
    cfg.write_text("# cartograph is neat\n[mcp_servers.other]\n")
    assert not _detect_mcp("codex", str(tmp_path))[0]
    cfg.write_text("[mcp_servers.cartograph]\ncommand = \"cartograph-mcp\"\n")
    assert _detect_mcp("codex", str(tmp_path))[0]


def test_mcp_instructions_are_trimmed():
    """MCP variant keeps domains + escape hatch, drops the command catalog."""
    from cartograph.cli import _build_setup_instructions_mcp
    content = _build_setup_instructions_mcp()
    assert "## Cartograph" in content
    assert "### Domains" in content
    assert "### Custom rules (project memory)" in content
    assert "Beyond the MCP surface" in content
    assert "### Commands" not in content
    assert "### Config keys" not in content


def test_full_and_mcp_setup_both_include_custom_rules():
    """Custom rules are ambient project memory — present in both setup modes."""
    from cartograph.cli import _build_setup_instructions, _build_setup_instructions_mcp
    full = _build_setup_instructions()
    mcp = _build_setup_instructions_mcp()
    for content in (full, mcp):
        assert "### Custom rules (project memory)" in content
        assert "hard memory" in content
        assert "cg-rules" in content
    # Full still carries command catalog; MCP stays trimmed
    assert "### Config keys" in full
    assert "### Config keys" not in mcp


def test_setup_mcp_flag_writes_trimmed(tmp_path):
    """--mcp forces the trimmed instructions."""
    import subprocess
    os.makedirs(tmp_path / ".claude")
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "setup", "--mcp"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
        env=_clean_subprocess_env(),
    )
    assert result.returncode == 0
    assert "MCP mode" in result.stdout
    content = (tmp_path / "CLAUDE.md").read_text()
    assert "Beyond the MCP surface" in content
    assert "### Commands" not in content


def test_setup_autodetects_mcp_from_project_config(tmp_path):
    """Bare setup with a cartograph server in .mcp.json writes trimmed
    instructions and says why."""
    import json
    import subprocess
    os.makedirs(tmp_path / ".claude")
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"cartograph": {"command": "cartograph-mcp"}}}))
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "setup"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
        env=_clean_subprocess_env(),
    )
    assert result.returncode == 0
    assert "MCP mode" in result.stdout
    assert ".mcp.json" in result.stdout
    content = (tmp_path / "CLAUDE.md").read_text()
    assert "Beyond the MCP surface" in content
    assert "### Commands" not in content


def test_setup_no_mcp_overrides_detection(tmp_path):
    """--no-mcp forces the full reference even when a server is configured."""
    import json
    import subprocess
    os.makedirs(tmp_path / ".claude")
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"cartograph": {"command": "cartograph-mcp"}}}))
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "setup", "--no-mcp"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
        env=_clean_subprocess_env(),
    )
    assert result.returncode == 0
    content = (tmp_path / "CLAUDE.md").read_text()
    assert "### Commands" in content
    assert "Beyond the MCP surface" not in content


def test_setup_mcp_with_workflow(tmp_path):
    """Workflow layer composes with the MCP variant unchanged."""
    import subprocess
    os.makedirs(tmp_path / ".claude")
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "setup", "--mcp", "--workflow"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
        env=_clean_subprocess_env(),
    )
    assert result.returncode == 0
    content = (tmp_path / "CLAUDE.md").read_text()
    assert "Beyond the MCP surface" in content
    assert "### Workflow" in content


def test_setup_overwrite_preserves_mcp_mode(tmp_path):
    """--overwrite re-detects MCP and keeps the trimmed form + workflow."""
    import json
    import subprocess
    os.makedirs(tmp_path / ".claude")
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"cartograph": {}}}))
    env = _clean_subprocess_env()
    subprocess.run(
        [sys.executable, "-m", "cartograph", "setup", "--workflow"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path), env=env)
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "setup", "--overwrite"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path), env=env)
    assert result.returncode == 0
    content = (tmp_path / "CLAUDE.md").read_text()
    assert "Beyond the MCP surface" in content
    assert "### Commands" not in content
    assert "### Workflow" in content
