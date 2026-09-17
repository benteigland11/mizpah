"""Tests for custom validation rules system."""
import sys
import json
import os

import pytest


# ---------------------------------------------------------------------------
# _rules_file_paths / find_rules
# ---------------------------------------------------------------------------

def test_finds_project_rules_file(tmp_path, monkeypatch):
    """Should find rules.py in .cartograph/rules/."""
    rules_dir = tmp_path / ".cartograph" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "rules.py").write_text("# rules")

    monkeypatch.chdir(tmp_path)
    from cartograph.rules import _rules_file_paths
    files = _rules_file_paths("python")
    project_files = [f for f in files if f["scope"] == "project"]
    assert len(project_files) == 1
    assert project_files[0]["scope"] == "project"


def test_ignores_wrong_language(tmp_path, monkeypatch):
    """Python project rules.py should not appear in javascript results."""
    rules_dir = tmp_path / ".cartograph" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "rules.py").write_text("# python rules")

    monkeypatch.chdir(tmp_path)
    from cartograph.rules import _rules_file_paths
    files = _rules_file_paths("javascript")
    project_files = [f for f in files if f["scope"] == "project"]
    assert len(project_files) == 0


def test_both_project_and_global(tmp_path, monkeypatch):
    """Both project and global rules files should be found."""
    from cartograph.engine import _user_data_dir
    global_dir = os.path.join(_user_data_dir(), "rules")
    os.makedirs(global_dir, exist_ok=True)
    global_file = os.path.join(global_dir, "rules.py")
    with open(global_file, "w") as f:
        f.write("# global")

    project_dir = tmp_path / ".cartograph" / "rules"
    project_dir.mkdir(parents=True)
    (project_dir / "rules.py").write_text("# project")

    monkeypatch.chdir(tmp_path)
    from cartograph.rules import _rules_file_paths
    files = _rules_file_paths("python")
    assert len(files) == 2
    assert files[0]["scope"] == "project"
    assert files[1]["scope"] == "global"

    # Cleanup
    os.remove(global_file)


def test_no_project_rules(tmp_path, monkeypatch):
    """No project rules dir means no project-scoped entries."""
    monkeypatch.chdir(tmp_path)
    from cartograph.rules import _rules_file_paths
    files = _rules_file_paths("python")
    project_files = [f for f in files if f["scope"] == "project"]
    assert project_files == []


# ---------------------------------------------------------------------------
# Org rules (CARTOGRAPH_ORG_RULES env var)
# ---------------------------------------------------------------------------

def test_org_rules_directory_entry(tmp_path, monkeypatch):
    """Directory entry in CARTOGRAPH_ORG_RULES should resolve to <dir>/rules.py."""
    org_dir = tmp_path / "org-rules"
    org_dir.mkdir()
    (org_dir / "rules.py").write_text("# org")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CARTOGRAPH_ORG_RULES", str(org_dir))
    from cartograph.rules import _rules_file_paths
    files = _rules_file_paths("python")
    org_files = [f for f in files if f["scope"] == "org"]
    assert len(org_files) == 1
    assert org_files[0]["path"] == str(org_dir / "rules.py")


def test_org_rules_file_entry(tmp_path, monkeypatch):
    """File entry in CARTOGRAPH_ORG_RULES should be used directly when filename matches."""
    org_file = tmp_path / "rules.py"
    org_file.write_text("# org")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CARTOGRAPH_ORG_RULES", str(org_file))
    from cartograph.rules import _rules_file_paths
    files = _rules_file_paths("python")
    org_files = [f for f in files if f["scope"] == "org"]
    assert len(org_files) == 1


def test_org_rules_file_wrong_language_ignored(tmp_path, monkeypatch):
    """A file entry whose basename doesn't match the language filename is skipped."""
    org_file = tmp_path / "rules.js"
    org_file.write_text("// org")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CARTOGRAPH_ORG_RULES", str(org_file))
    from cartograph.rules import _rules_file_paths
    files = _rules_file_paths("python")
    assert [f for f in files if f["scope"] == "org"] == []


def test_org_rules_multiple_paths(tmp_path, monkeypatch):
    """Colon-separated paths should each contribute org rules."""
    a = tmp_path / "a"; a.mkdir(); (a / "rules.py").write_text("# a")
    b = tmp_path / "b"; b.mkdir(); (b / "rules.py").write_text("# b")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CARTOGRAPH_ORG_RULES", f"{a}{os.pathsep}{b}")
    from cartograph.rules import _rules_file_paths
    files = _rules_file_paths("python")
    org_files = [f for f in files if f["scope"] == "org"]
    assert len(org_files) == 2


def test_org_rules_failure_tagged(tmp_path, monkeypatch):
    """Org-rule blocks should be tagged [org] in their messages."""
    org_dir = tmp_path / "org-rules"
    org_dir.mkdir()
    (org_dir / "rules.py").write_text(
        'import json\n'
        'print(json.dumps({"blocks": ["banned thing"], "warnings": []}))\n'
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CARTOGRAPH_ORG_RULES", str(org_dir))
    from cartograph.rules import run_all_rules
    result = run_all_rules(str(tmp_path), "python")
    assert any("[org]" in msg and "banned thing" in msg for msg in result["blocks"])


def test_org_rules_empty_env_no_op(tmp_path, monkeypatch):
    """Empty/unset CARTOGRAPH_ORG_RULES yields no org entries."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CARTOGRAPH_ORG_RULES", raising=False)
    from cartograph.rules import _rules_file_paths
    assert [f for f in _rules_file_paths("python") if f["scope"] == "org"] == []


# ---------------------------------------------------------------------------
# _run_rules_file
# ---------------------------------------------------------------------------

def test_run_passing_rules(tmp_path, monkeypatch):
    """A rules file that outputs empty blocks/warnings should pass."""
    monkeypatch.chdir(tmp_path)
    rules_file = tmp_path / "rules.py"
    rules_file.write_text(
        'import json, sys\n'
        'print(json.dumps({"blocks": [], "warnings": []}))\n'
    )
    from cartograph.rules import _run_rules_file
    result = _run_rules_file(str(rules_file), str(tmp_path), "python")
    assert result["blocks"] == []
    assert result["warnings"] == []


def test_run_rules_with_warnings(tmp_path, monkeypatch):
    """Warnings should be tagged with scope."""
    monkeypatch.chdir(tmp_path)
    rules_file = tmp_path / ".cartograph" / "rules" / "rules.py"
    rules_file.parent.mkdir(parents=True)
    rules_file.write_text(
        'import json, sys\n'
        'print(json.dumps({"blocks": [], "warnings": ["too many lines"]}))\n'
    )
    from cartograph.rules import _run_rules_file
    result = _run_rules_file(str(rules_file), str(tmp_path), "python", "project")
    assert len(result["warnings"]) == 1
    assert "[project]" in result["warnings"][0]
    assert "too many lines" in result["warnings"][0]


def test_run_rules_with_blocks(tmp_path, monkeypatch):
    """Blocks should be tagged with scope."""
    monkeypatch.chdir(tmp_path)
    rules_file = tmp_path / "rules.py"
    rules_file.write_text(
        'import json, sys\n'
        'print(json.dumps({"blocks": ["banned import"], "warnings": []}))\n'
    )
    from cartograph.rules import _run_rules_file
    result = _run_rules_file(str(rules_file), str(tmp_path), "python")
    assert len(result["blocks"]) == 1
    assert "banned import" in result["blocks"][0]


def test_run_rules_nonzero_exit(tmp_path, monkeypatch):
    """Non-zero exit should produce a block with helpful error message."""
    monkeypatch.chdir(tmp_path)
    rules_file = tmp_path / "rules.py"
    rules_file.write_text('raise ValueError("broken")\n')
    from cartograph.rules import _run_rules_file
    result = _run_rules_file(str(rules_file), str(tmp_path), "python")
    assert len(result["blocks"]) == 1
    assert "script error" in result["blocks"][0]
    assert "must exit 0" in result["blocks"][0]


def test_run_rules_invalid_json(tmp_path, monkeypatch):
    """Invalid JSON output should produce a helpful error."""
    monkeypatch.chdir(tmp_path)
    rules_file = tmp_path / "rules.py"
    rules_file.write_text('print("not json")\n')
    from cartograph.rules import _run_rules_file
    result = _run_rules_file(str(rules_file), str(tmp_path), "python")
    assert len(result["blocks"]) == 1
    assert "invalid JSON" in result["blocks"][0]
    assert '"blocks"' in result["blocks"][0]  # shows the expected format


def test_run_rules_empty_output_passes(tmp_path, monkeypatch):
    """Empty stdout = all checks passed."""
    monkeypatch.chdir(tmp_path)
    rules_file = tmp_path / "rules.py"
    rules_file.write_text("# does nothing\n")
    from cartograph.rules import _run_rules_file
    result = _run_rules_file(str(rules_file), str(tmp_path), "python")
    assert result["blocks"] == []
    assert result["warnings"] == []


def test_run_rules_receives_widget_path(tmp_path, monkeypatch):
    """Rules file should receive widget path as argv[1]."""
    monkeypatch.chdir(tmp_path)
    widget_dir = tmp_path / "my_widget"
    widget_dir.mkdir()
    rules_file = tmp_path / "rules.py"
    rules_file.write_text(
        'import json, sys, os\n'
        'wp = sys.argv[1]\n'
        'exists = os.path.isdir(wp)\n'
        'print(json.dumps({"blocks": [] if exists else ["not found"], "warnings": []}))\n'
    )
    from cartograph.rules import _run_rules_file
    result = _run_rules_file(str(rules_file), str(widget_dir), "python")
    assert result["blocks"] == []


def test_run_rules_bad_types(tmp_path, monkeypatch):
    """blocks/warnings that aren't arrays should produce helpful error."""
    monkeypatch.chdir(tmp_path)
    rules_file = tmp_path / "rules.py"
    rules_file.write_text(
        'import json\n'
        'print(json.dumps({"blocks": "not a list", "warnings": []}))\n'
    )
    from cartograph.rules import _run_rules_file
    result = _run_rules_file(str(rules_file), str(tmp_path), "python")
    assert len(result["blocks"]) == 1
    assert "must be JSON arrays" in result["blocks"][0]


# ---------------------------------------------------------------------------
# run_all_rules
# ---------------------------------------------------------------------------

def test_run_all_merges_project_and_global(tmp_path, monkeypatch):
    """Both project and global rules should run and merge."""
    from cartograph.engine import _user_data_dir
    global_dir = os.path.join(_user_data_dir(), "rules")
    os.makedirs(global_dir, exist_ok=True)
    global_file = os.path.join(global_dir, "rules.py")
    with open(global_file, "w") as f:
        f.write(
            'import json, sys\n'
            'print(json.dumps({"blocks": [], "warnings": ["global warn"]}))\n'
        )

    project_dir = tmp_path / ".cartograph" / "rules"
    project_dir.mkdir(parents=True)
    (project_dir / "rules.py").write_text(
        'import json, sys\n'
        'print(json.dumps({"blocks": ["project block"], "warnings": []}))\n'
    )

    monkeypatch.chdir(tmp_path)
    from cartograph.rules import run_all_rules
    result = run_all_rules(str(tmp_path), "python")
    assert result["rules_run"] == 2
    assert len(result["blocks"]) == 1
    assert len(result["warnings"]) == 1

    # Cleanup
    os.remove(global_file)


def test_run_all_unsupported_language(tmp_path, monkeypatch):
    """Unsupported language should return empty with rules_run=0."""
    monkeypatch.chdir(tmp_path)
    from cartograph.rules import run_all_rules
    result = run_all_rules(str(tmp_path), "cobol")
    assert result["rules_run"] == 0
    assert result["blocks"] == []


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def test_template_python_valid_syntax():
    """Python template should be syntactically valid."""
    from cartograph.rules import get_template
    tmpl = get_template("python")
    assert tmpl is not None
    compile(tmpl, "<template>", "exec")


def test_template_none_for_unknown():
    """Unknown language should return None."""
    from cartograph.rules import get_template
    assert get_template("cobol") is None


def test_rules_filename():
    """Should return correct filenames."""
    from cartograph.rules import get_rules_filename
    assert get_rules_filename("python") == "rules.py"
    assert get_rules_filename("javascript") == "rules.js"
    assert get_rules_filename("typescript") == "rules.typescript.js"
    assert get_rules_filename("nim") == "rules.nim"
    assert get_rules_filename("angular") == "rules.angular.js"
    assert get_rules_filename("php") == "rules.php"
    assert get_rules_filename("openscad") == "rules.openscad.py"
    assert get_rules_filename("systemverilog") == "rules.sv.py"
    assert get_rules_filename("css") == "rules.css.js"
    assert get_rules_filename("cobol") is None


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_rules_init(tmp_path):
    """cartograph rules init should create the rules file."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "rules", "init", "--language", "python"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert "Created" in result.stdout
    rules_file = tmp_path / ".cartograph" / "rules" / "rules.py"
    assert rules_file.exists()
    content = rules_file.read_text()
    assert "def validate" in content


def test_cli_rules_init_already_exists(tmp_path):
    """Running init twice should tell you the file exists."""
    import subprocess
    subprocess.run(
        [sys.executable, "-m", "cartograph", "rules", "init", "--language", "python"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
    )
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "rules", "init", "--language", "python"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert "already exists" in result.stdout


def test_cli_rules_reset(tmp_path):
    """Reset should restore the default template over a broken file."""
    import subprocess
    # Init, then break it
    subprocess.run(
        [sys.executable, "-m", "cartograph", "rules", "init", "--language", "python"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
    )
    rules_file = tmp_path / ".cartograph" / "rules" / "rules.py"
    rules_file.write_text("BROKEN SYNTAX {{{{")

    # Reset without --confirm should block as an error (CLI contract:
    # refusals exit non-zero so agents/CI notice).
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "rules", "reset", "--language", "python"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
    )
    assert result.returncode != 0
    assert "Re-run with --confirm" in result.stdout

    # Reset with --confirm should succeed
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "rules", "reset", "--language", "python", "--confirm"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert "Reset to default" in result.stdout

    # Should be valid again
    content = rules_file.read_text()
    assert "def validate" in content
    compile(content, "<rules>", "exec")


def test_cli_rules_list_shows_global(tmp_path):
    """Listing should show global rules that were auto-created."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "cartograph", "rules"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert "global" in result.stdout
