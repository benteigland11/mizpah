"""
CLI output contract tests.

The MCP layer and agent integrations depend on `cartograph <cmd>` writing
parseable JSON to stdout. Any prose or progress commentary belongs on stderr.

These tests invoke the CLI as a subprocess with an isolated library path
(WIDGET_LIBRARY_PATH) and validate:
  * stdout is parseable JSON
  * the result dict has the expected shape
  * cloud-touching commands either no-op or route to stderr when unauth'd
"""
import json
import os
import shutil
import subprocess
import sys
import pytest


@pytest.fixture
def tmp_library(fixture_library, tmp_path):
    dst = tmp_path / "Widget_Library"
    shutil.copytree(fixture_library, dst)
    return str(dst)


@pytest.fixture
def cli_env(tmp_library, tmp_path):
    """Environment for an isolated CLI invocation.

    Points at the fixture library and a fresh HOME so per-user state
    (config, credentials) doesn't leak in or out.
    """
    env = dict(os.environ)
    env["WIDGET_LIBRARY_PATH"] = tmp_library
    # Redirect per-user state to a scratch dir without touching HOME (that
    # would break site-packages resolution when cartograph is --user-installed).
    env["XDG_DATA_HOME"] = str(tmp_path / "xdg-data")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg-config")
    # Ensure src/ (cartograph) and the repo root (the cg/ widget tree) are
    # importable regardless of how cartograph was installed. Without the repo
    # root, `import cg` falls through to any stale --user-installed copy.
    repo_root = os.path.dirname(os.path.dirname(__file__))
    repo_src = os.path.join(repo_root, "src")
    env["PYTHONPATH"] = os.pathsep.join(
        [repo_src, repo_root, env.get("PYTHONPATH", "")]
    )
    return env


def _run(args, env, cwd):
    """Run cartograph CLI, return (returncode, stdout_str, stderr_str)."""
    cmd = [sys.executable, "-m", "cartograph"] + args
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def _parse_json_stdout(stdout, stderr):
    """Parse stdout as JSON, with a helpful failure message if it isn't."""
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        pytest.fail(f"stdout is not valid JSON: {e}\n\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}")


# ---------------------------------------------------------------------------
# Read-only commands — must emit JSON
# ---------------------------------------------------------------------------

def test_search_emits_json(cli_env, tmp_path):
    rc, stdout, stderr = _run(["search", "http"], cli_env, str(tmp_path))
    result = _parse_json_stdout(stdout, stderr)
    assert isinstance(result, dict)
    # Should surface the http-client fixture widget in the local payload
    widgets = (result.get("local") or {}).get("widgets") or []
    ids = [w.get("id") for w in widgets]
    assert "http-client" in ids, f"search did not find http-client: {ids}"


def test_inspect_emits_json(cli_env, tmp_path):
    rc, stdout, stderr = _run(["inspect", "http-client"], cli_env, str(tmp_path))
    result = _parse_json_stdout(stdout, stderr)
    assert isinstance(result, dict)
    assert result.get("id") == "http-client" or result.get("widget_id") == "http-client" \
           or (result.get("meta") or {}).get("id") == "http-client"


def test_config_list_prose_default(cli_env, tmp_path):
    """Without --json, config list emits human-friendly prose, not JSON."""
    rc, stdout, stderr = _run(["config"], cli_env, str(tmp_path))
    assert rc == 0
    # Prose output lists keys; should not parse as JSON
    assert "auto-publish" in stdout
    import json as _json
    try:
        _json.loads(stdout)
        pytest.fail("config list without --json should emit prose, got parseable JSON")
    except _json.JSONDecodeError:
        pass  # expected


def test_config_list_json_flag(cli_env, tmp_path):
    """With --json, config list emits structured settings for MCP consumption."""
    rc, stdout, stderr = _run(["config", "--json"], cli_env, str(tmp_path))
    result = _parse_json_stdout(stdout, stderr)
    assert result.get("status") == "success"
    settings = result.get("settings", [])
    assert isinstance(settings, list) and len(settings) > 0
    row = settings[0]
    assert "key" in row and "description" in row


def test_config_get_unknown_key_emits_json_error(cli_env, tmp_path):
    """Errors always emit JSON via err(), regardless of --json flag."""
    rc, stdout, stderr = _run(["config", "nonexistent-key-xyz"], cli_env, str(tmp_path))
    assert rc != 0
    result = _parse_json_stdout(stdout, stderr)
    assert "error" in result or result.get("status") == "error"


def test_stats_emits_json(cli_env, tmp_path):
    rc, stdout, stderr = _run(["stats"], cli_env, str(tmp_path))
    result = _parse_json_stdout(stdout, stderr)
    assert isinstance(result, dict)
    assert result.get("widget_count", 0) >= 1
    assert "by_language" in result and "by_domain" in result


def test_status_no_installs_emits_json(cli_env, tmp_path):
    """In a dir with no cg/, status should return empty list as JSON."""
    project = tmp_path / "empty-project"
    project.mkdir()
    rc, stdout, stderr = _run(["status"], cli_env, str(project))
    result = _parse_json_stdout(stdout, stderr)
    assert result.get("widgets") == []


# ---------------------------------------------------------------------------
# Write commands — exercise real install/status/uninstall end-to-end
# ---------------------------------------------------------------------------

def test_install_emits_json(cli_env, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    rc, stdout, stderr = _run(["install", "http-client"], cli_env, str(project))
    result = _parse_json_stdout(stdout, stderr)
    assert result.get("status") == "success", result
    assert os.path.isdir(project / "cg" / "http-client")


def test_install_unknown_widget_emits_json_error(cli_env, tmp_path):
    """Unknown widget must emit parseable JSON carrying an error AND exit non-zero."""
    project = tmp_path / "project"
    project.mkdir()
    rc, stdout, stderr = _run(["install", "nonexistent-widget-xyz"], cli_env, str(project))
    assert rc != 0, "install of unknown widget should exit non-zero so agents/CI notice"
    result = _parse_json_stdout(stdout, stderr)
    assert "error" in result or result.get("status") == "error"


def test_install_then_status_then_uninstall_flow(cli_env, tmp_path):
    """End-to-end agent flow: install → status (sees it) → uninstall (gone)."""
    project = tmp_path / "project"
    project.mkdir()

    # install
    rc, stdout, stderr = _run(["install", "http-client"], cli_env, str(project))
    result = _parse_json_stdout(stdout, stderr)
    assert result.get("status") == "success", result

    # status (bulk listing — no widget_dir arg). Note the list is elided when
    # everything is clean; installed count is the authoritative signal.
    rc, stdout, stderr = _run(["status"], cli_env, str(project))
    status = _parse_json_stdout(stdout, stderr)
    assert status.get("installed", 0) >= 1, f"status did not see the install: {status}"

    # uninstall (requires widget_dir path, not bare id)
    widget_dir = str(project / "cg" / "http-client")
    rc, stdout, stderr = _run(["uninstall", widget_dir], cli_env, str(project))
    result = _parse_json_stdout(stdout, stderr)
    assert result.get("status") == "success", result
    assert not os.path.isdir(widget_dir)


# ---------------------------------------------------------------------------
# create — library-path guard errors must still be JSON
# ---------------------------------------------------------------------------

def test_create_in_library_errors_with_json(cli_env, tmp_library):
    """cmd_create guard: creating inside the library must err as JSON, not prose."""
    rc, stdout, stderr = _run(
        ["create", "test-widget", "--language", "python",
         "--domain", "backend", "--target", tmp_library],
        cli_env, tmp_library,
    )
    assert rc != 0
    result = _parse_json_stdout(stdout, stderr)
    assert "error" in result or result.get("status") == "error"
    msg = result.get("error", "") or result.get("message", "")
    assert "library" in msg.lower()
