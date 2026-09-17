"""
Tests for the checkin → cloud push path (`_force_push`).

Fully mocks the cloud and auth layers — no network calls. Exercises:
  * Not-authenticated: silent no-op (stderr only, does not raise)
  * Own widget with sidecar: push, sidecar rewritten
  * Own widget no sidecar: push via config publish-registry
  * Origin differs from current user: routed as proposal, not push
"""
import json
import os
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture
def widget_dir(tmp_path):
    """A minimal installed-widget dir so _force_push has a real path to read/write."""
    wdir = tmp_path / "cg" / "http-client"
    wdir.mkdir(parents=True)
    (wdir / "widget.json").write_text(json.dumps({
        "meta": {"id": "http-client", "name": "HTTP Client",
                 "version": "1.2.1", "domain": "backend", "tags": ["http"]},
        "tech_stack": {"language": "python", "dependencies": []},
    }))
    return str(wdir)


@pytest.fixture
def checkin_result(widget_dir):
    return {
        "status": "success",
        "action": "updated",
        "id": "http-client",
        "path": widget_dir,
        "version": "1.2.1",
    }


# ---------------------------------------------------------------------------
# Not authenticated → no push, no crash
# ---------------------------------------------------------------------------

def test_force_push_bails_when_not_authenticated(checkin_result, widget_dir):
    from cartograph import cli
    with patch("cartograph.auth.is_authenticated", return_value=False), \
         patch("cartograph.cloud.push") as mock_push, \
         patch("cartograph.cloud.propose") as mock_propose:
        cli._force_push(checkin_result, install_path=widget_dir, reason="test")
    mock_push.assert_not_called()
    mock_propose.assert_not_called()


# ---------------------------------------------------------------------------
# Own widget → cloud.push called with config visibility/governance
# ---------------------------------------------------------------------------

def test_force_push_own_widget_no_sidecar(checkin_result, widget_dir):
    """No sidecar — falls back to publish-registry config and calls cloud.push."""
    from cartograph import cli
    with patch("cartograph.auth.is_authenticated", return_value=True), \
         patch("cartograph.cloud.push") as mock_push, \
         patch("cartograph.cloud.whoami", return_value={"owner": "alice"}), \
         patch("cartograph.cli._config_publish_registry_url", return_value=None), \
         patch("cartograph.config.load_config", return_value={
             "publish": {"visibility": "public", "governance": "protected", "auto_publish": False}
         }):
        mock_push.return_value = {
            "status": "success",
            "namespaced_id": "@alice/http-client",
            "version": "1.2.1",
            "governance": "protected",
        }
        cli._force_push(checkin_result, install_path=widget_dir, reason="test")

    mock_push.assert_called_once()
    kwargs = mock_push.call_args.kwargs
    assert kwargs["visibility"] == "public"
    assert kwargs["governance"] == "protected"


def test_force_push_writes_sidecar_on_success(checkin_result, widget_dir):
    """On successful push, a .cartograph_source sidecar is written."""
    from cartograph import cli
    with patch("cartograph.auth.is_authenticated", return_value=True), \
         patch("cartograph.cloud.push") as mock_push, \
         patch("cartograph.cloud.whoami", return_value={"owner": "alice"}), \
         patch("cartograph.cli._config_publish_registry_url", return_value=None), \
         patch("cartograph.config.load_config", return_value={
             "publish": {"visibility": "public", "governance": "protected", "auto_publish": False}
         }):
        mock_push.return_value = {
            "status": "success",
            "namespaced_id": "@alice/http-client",
            "version": "1.2.1",
            "governance": "protected",
        }
        cli._force_push(checkin_result, install_path=widget_dir, reason="test")

    sidecar_path = os.path.join(widget_dir, ".cartograph_source")
    assert os.path.isfile(sidecar_path)
    with open(sidecar_path) as f:
        sidecar = json.load(f)
    assert sidecar["owner"] == "alice"
    assert "registry_url" in sidecar


# ---------------------------------------------------------------------------
# Origin differs → proposal route, not push
# ---------------------------------------------------------------------------

def test_force_push_routes_as_proposal_when_origin_differs(checkin_result, widget_dir):
    """If sidecar.owner != current user, the edit is a proposal not a direct push."""
    # Write a sidecar indicating widget originated from someone else
    with open(os.path.join(widget_dir, ".cartograph_source"), "w") as f:
        json.dump({"owner": "bob", "registry_url": "https://registry.example.com"}, f)

    from cartograph import cli
    with patch("cartograph.auth.is_authenticated", return_value=True), \
         patch("cartograph.cloud.push") as mock_push, \
         patch("cartograph.cloud.propose") as mock_propose, \
         patch("cartograph.cloud.whoami", return_value={"owner": "alice"}):
        mock_propose.return_value = {"status": "proposed", "proposal_id": "prop-123"}
        cli._force_push(checkin_result, install_path=widget_dir, reason="improve docs")

    mock_push.assert_not_called()
    mock_propose.assert_called_once()
    args, kwargs = mock_propose.call_args
    # propose(widget_path, owner_handle, widget_id, reason=..., registry_url=...)
    assert args[1] == "bob"
    assert args[2] == "http-client"
    assert kwargs.get("reason") == "improve docs"
    assert kwargs.get("registry_url") == "https://registry.example.com"


def test_force_push_own_widget_with_sidecar_still_pushes(checkin_result, widget_dir):
    """Sidecar owner == current user: normal push using the sidecar's registry."""
    with open(os.path.join(widget_dir, ".cartograph_source"), "w") as f:
        json.dump({"owner": "alice", "registry_url": "https://myorg.example.com"}, f)

    from cartograph import cli
    with patch("cartograph.auth.is_authenticated", return_value=True), \
         patch("cartograph.cloud.push") as mock_push, \
         patch("cartograph.cloud.propose") as mock_propose, \
         patch("cartograph.cloud.whoami", return_value={"owner": "alice"}), \
         patch("cartograph.config.load_config", return_value={
             "publish": {"visibility": "public", "governance": "open", "auto_publish": True}
         }):
        mock_push.return_value = {
            "status": "success",
            "namespaced_id": "@alice/myorg-http-client",
            "version": "1.2.1",
            "governance": "open",
        }
        cli._force_push(checkin_result, install_path=widget_dir, reason="")

    mock_propose.assert_not_called()
    mock_push.assert_called_once()
    # Sidecar's registry_url should be the one used
    kwargs = mock_push.call_args.kwargs
    assert kwargs["registry_url"] == "https://myorg.example.com"


# ---------------------------------------------------------------------------
# Prefix reconstruction in the install hint
# ---------------------------------------------------------------------------

def test_force_push_reconstructs_public_prefix(checkin_result, widget_dir, capsys):
    """A push routed at the public registry must produce `@owner/cg-<bare>`
    in the printed install hint."""
    from cartograph import cli
    with patch("cartograph.auth.is_authenticated", return_value=True), \
         patch("cartograph.cloud.push") as mock_push, \
         patch("cartograph.cloud.whoami", return_value={"owner": "alice"}), \
         patch("cartograph.cli._config_publish_registry_url", return_value=None), \
         patch("cartograph.config.get_registries", return_value=[]), \
         patch("cartograph.config.load_config", return_value={
             "publish": {"visibility": "public", "governance": "open", "auto_publish": True}
         }):
        mock_push.return_value = {
            "status": "success",
            "namespaced_id": "@alice/http-client",
            "version": "1.2.1",
            "governance": "open",
        }
        cli._force_push(checkin_result, install_path=widget_dir, reason="")

    err = capsys.readouterr().err
    assert "cartograph install @alice/cg-http-client" in err


def test_force_push_reconstructs_configured_registry_prefix(checkin_result, widget_dir, capsys):
    """When push routes to a user-configured registry, the printed install
    hint must use that registry's prefix rather than `cg-`."""
    # Sidecar pinning to the configured registry
    with open(os.path.join(widget_dir, ".cartograph_source"), "w") as f:
        json.dump({"owner": "alice",
                   "registry_url": "https://reg.myorg.test"}, f)
    from cartograph import cli
    fake_registries = [{"prefix": "myorg", "url": "https://reg.myorg.test"}]
    with patch("cartograph.auth.is_authenticated", return_value=True), \
         patch("cartograph.cloud.push") as mock_push, \
         patch("cartograph.cloud.whoami", return_value={"owner": "alice"}), \
         patch("cartograph.config.get_registries", return_value=fake_registries), \
         patch("cartograph.config.load_config", return_value={
             "publish": {"visibility": "public", "governance": "open", "auto_publish": True}
         }):
        mock_push.return_value = {
            "status": "success",
            "namespaced_id": "@alice/http-client",
            "version": "1.2.1",
            "governance": "open",
        }
        cli._force_push(checkin_result, install_path=widget_dir, reason="")

    err = capsys.readouterr().err
    assert "cartograph install @alice/myorg-http-client" in err


def test_force_push_bare_namespaced_id_passthrough(checkin_result, widget_dir, capsys):
    """If push returns a namespaced_id without `/`, it should be printed
    as-is (no synthetic prefix grafting)."""
    from cartograph import cli
    with patch("cartograph.auth.is_authenticated", return_value=True), \
         patch("cartograph.cloud.push") as mock_push, \
         patch("cartograph.cloud.whoami", return_value={"owner": "alice"}), \
         patch("cartograph.cli._config_publish_registry_url", return_value=None), \
         patch("cartograph.config.load_config", return_value={
             "publish": {"visibility": "public", "governance": "open", "auto_publish": True}
         }):
        mock_push.return_value = {
            "status": "success",
            "namespaced_id": "http-client",  # no slash
            "version": "1.2.1",
            "governance": "open",
        }
        cli._force_push(checkin_result, install_path=widget_dir, reason="")

    err = capsys.readouterr().err
    assert "cartograph install http-client" in err


# ---------------------------------------------------------------------------
# Proposal failure path
# ---------------------------------------------------------------------------

def test_force_push_proposal_failure_returns_error(checkin_result, widget_dir):
    """When cloud.propose returns an error, _force_push must return the
    structured error result (not silently succeed)."""
    with open(os.path.join(widget_dir, ".cartograph_source"), "w") as f:
        json.dump({"owner": "bob", "registry_url": "https://registry.example.com"}, f)

    from cartograph import cli
    with patch("cartograph.auth.is_authenticated", return_value=True), \
         patch("cartograph.cloud.push") as mock_push, \
         patch("cartograph.cloud.propose") as mock_propose, \
         patch("cartograph.cloud.whoami", return_value={"owner": "alice"}):
        mock_propose.return_value = {"error": "rejected by governance hook"}
        result = cli._force_push(checkin_result, install_path=widget_dir,
                                 reason="improve")
    mock_push.assert_not_called()
    assert result["status"] == "error"
    assert result["kind"] == "proposal"
    assert "rejected by governance hook" in result["error"]


# ---------------------------------------------------------------------------
# Push failure → no crash, no sidecar write
# ---------------------------------------------------------------------------

def test_force_push_handles_push_error_gracefully(checkin_result, widget_dir):
    """A push failure should log to stderr but not raise or write a sidecar."""
    from cartograph import cli
    with patch("cartograph.auth.is_authenticated", return_value=True), \
         patch("cartograph.cloud.push") as mock_push, \
         patch("cartograph.cloud.whoami", return_value={"owner": "alice"}), \
         patch("cartograph.cli._config_publish_registry_url", return_value=None), \
         patch("cartograph.config.load_config", return_value={
             "publish": {"visibility": "public", "governance": "protected", "auto_publish": False}
         }):
        mock_push.return_value = {"error": "registry unreachable"}
        # Should not raise
        cli._force_push(checkin_result, install_path=widget_dir, reason="test")

    # No sidecar written on failure
    assert not os.path.isfile(os.path.join(widget_dir, ".cartograph_source"))


# ---------------------------------------------------------------------------
# cmd_checkin: tri-state publish decision
# ---------------------------------------------------------------------------

def _checkin_args(path, **overrides):
    """Build a SimpleNamespace mimicking argparse output for cmd_checkin."""
    from types import SimpleNamespace
    return SimpleNamespace(
        path=path, reason="r", bump="minor",
        publish=overrides.get("publish", False),
        no_publish=overrides.get("no_publish", False),
        override_warnings=False, override_reason=None,
    )


def test_cmd_checkin_no_publish_overrides_auto_publish_true(checkin_result, widget_dir):
    """Regression: agent passes publish=False (→ --no-publish), and even with
    auto_publish=True the CLI must NOT push to cloud."""
    from cartograph import cli
    fake_carto = MagicMock()
    fake_carto.checkin.return_value = checkin_result
    with patch.object(cli, "_resolve_widget", return_value=widget_dir), \
         patch.object(cli, "_preflight_from_path"), \
         patch.object(cli, "_carto", return_value=fake_carto), \
         patch.object(cli, "_force_push") as mock_push, \
         patch.object(cli, "out"), \
         patch("cartograph.config.load_config", return_value={
             "publish": {"visibility": "public", "governance": "open", "auto_publish": True}
         }):
        cli.cmd_checkin(_checkin_args(widget_dir, no_publish=True))
    mock_push.assert_not_called()


def test_cmd_checkin_publish_flag_overrides_auto_publish_false(checkin_result, widget_dir):
    """Explicit --publish must push even when auto_publish=False."""
    from cartograph import cli
    fake_carto = MagicMock()
    fake_carto.checkin.return_value = checkin_result
    with patch.object(cli, "_resolve_widget", return_value=widget_dir), \
         patch.object(cli, "_preflight_from_path"), \
         patch.object(cli, "_carto", return_value=fake_carto), \
         patch.object(cli, "_force_push", return_value={"status": "success"}) as mock_push, \
         patch.object(cli, "out"), \
         patch("cartograph.config.load_config", return_value={
             "publish": {"visibility": "public", "governance": "open", "auto_publish": False}
         }):
        cli.cmd_checkin(_checkin_args(widget_dir, publish=True))
    mock_push.assert_called_once()


def test_cmd_checkin_unspecified_falls_back_to_auto_publish_true(checkin_result, widget_dir):
    """Neither flag set → fall back to auto_publish=True → push."""
    from cartograph import cli
    fake_carto = MagicMock()
    fake_carto.checkin.return_value = checkin_result
    with patch.object(cli, "_resolve_widget", return_value=widget_dir), \
         patch.object(cli, "_preflight_from_path"), \
         patch.object(cli, "_carto", return_value=fake_carto), \
         patch.object(cli, "_force_push", return_value={"status": "success"}) as mock_push, \
         patch.object(cli, "out"), \
         patch("cartograph.config.load_config", return_value={
             "publish": {"visibility": "public", "governance": "open", "auto_publish": True}
         }):
        cli.cmd_checkin(_checkin_args(widget_dir))
    mock_push.assert_called_once()


def test_cmd_checkin_unspecified_falls_back_to_auto_publish_false(checkin_result, widget_dir):
    """Neither flag set → fall back to auto_publish=False → no push."""
    from cartograph import cli
    fake_carto = MagicMock()
    fake_carto.checkin.return_value = checkin_result
    with patch.object(cli, "_resolve_widget", return_value=widget_dir), \
         patch.object(cli, "_preflight_from_path"), \
         patch.object(cli, "_carto", return_value=fake_carto), \
         patch.object(cli, "_force_push") as mock_push, \
         patch.object(cli, "out"), \
         patch("cartograph.config.load_config", return_value={
             "publish": {"visibility": "public", "governance": "open", "auto_publish": False}
         }):
        cli.cmd_checkin(_checkin_args(widget_dir))
    mock_push.assert_not_called()
