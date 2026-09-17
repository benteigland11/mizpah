"""
Tests for widget scaffolding via create().
create() returns {"status": "success", "path": ..., "item_id": ...}
or {"status": "error", "message": ...}
"""
import os
import json
import pytest


def _assert_scaffold(widget_dir, expected_paths):
    for rel_path in expected_paths:
        full = os.path.join(widget_dir, rel_path)
        assert os.path.exists(full), f"Expected scaffold path missing: {rel_path}"


def test_create_python_widget(carto, tmp_path):
    result = carto.create(
        "my-widget",
        language="python",
        name="My Widget",
        domain="backend",
        tags=["test"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], ["widget.json", "src", "tests", "examples"])


def test_create_javascript_widget(carto, tmp_path):
    result = carto.create(
        "my-js-widget",
        language="javascript",
        name="My JS Widget",
        domain="frontend",
        tags=["ui"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], ["widget.json", "src", "tests"])


def test_create_widget_manifest_valid(carto, tmp_path):
    result = carto.create(
        "manifest-widget",
        language="python",
        name="Manifest Widget",
        domain="backend",
        tags=["manifest", "test"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    manifest_path = os.path.join(result["path"], "widget.json")
    assert os.path.exists(manifest_path)
    with open(manifest_path) as f:
        data = json.load(f)
    meta = data.get("meta", data)
    # create() appends the language to the id (e.g. "manifest-widget-python")
    assert "manifest-widget" in meta.get("id", "")
    assert "tags" in meta


def test_create_angular_widget(carto, tmp_path):
    result = carto.create(
        "my-ng-widget",
        language="angular",
        name="My Angular Widget",
        domain="frontend",
        tags=["ui"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    # scaffolding/__init__.py converts hyphens to underscores in module_name
    _assert_scaffold(result["path"], [
        "widget.json",
        "angular.json",
        "package.json",
        "karma.conf.js",
        "ng-package.json",
        "tsconfig.json",
        "tsconfig.lib.json",
        "tsconfig.spec.json",
        "src/test.ts",
        "src/public-api.ts",
        "src/my_ng_widget.component.ts",
        "tests/test_my_ng_widget.component.ts",
        "examples/example_usage.ts",
    ])


def test_create_php_widget(carto, tmp_path):
    result = carto.create(
        "my-php-widget",
        language="php",
        name="My PHP Widget",
        domain="backend",
        tags=["utility"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], [
        "widget.json",
        "composer.json",
        "phpunit.xml",
        "src/MyPhpWidget.php",
        "tests/MyPhpWidgetTest.php",
        "examples/example_usage.php",
    ])


def test_create_terraform_widget(carto, tmp_path):
    result = carto.create(
        "my-tf-widget",
        language="terraform",
        name="My TF Widget",
        domain="devops",
        tags=["aws", "terraform"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], [
        "widget.json",
        "src/my_tf_widget.tf",
        "src/variables.tf",
        "src/outputs.tf",
        "src/versions.tf",
        "tests/test_my_tf_widget.tf",
        "examples/example_usage.tf",
    ])


def test_create_duplicate_widget_errors(carto, tmp_path):
    carto.create("dupe-widget", language="python", name="Dupe", domain="backend", tags=[], target_dir=str(tmp_path))
    result = carto.create("dupe-widget", language="python", name="Dupe", domain="backend", tags=[], target_dir=str(tmp_path))
    assert result.get("status") == "error"


def test_create_go_widget(carto, tmp_path, monkeypatch):
    # Go ships supported=False until its stress test passes; the scaffold
    # itself is testable regardless of the ship gate.
    from cartograph.languages.go import GoEngine
    monkeypatch.setattr(GoEngine, "supported", True)
    result = carto.create(
        "my-go-widget",
        language="go",
        name="My Go Widget",
        domain="backend",
        tags=["utility"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], [
        "widget.json",
        "go.mod",
        "src/my_go_widget.go",
        "tests/my_go_widget_test.go",
        "examples/example_usage.go",
    ])
    # The module path in go.mod must match what the test/example imports.
    with open(f"{result['path']}/go.mod") as f:
        assert "module my_go_widget" in f.read()


def test_create_rust_widget(carto, tmp_path, monkeypatch):
    # Rust ships supported=False until its cross-platform CI proves the
    # cargo-llvm-cov toolchain; the scaffold itself is testable regardless.
    from cartograph.languages.rust import RustEngine
    monkeypatch.setattr(RustEngine, "supported", True)
    result = carto.create(
        "my-widget",
        language="rust",
        name="My Widget",
        domain="backend",
        tags=["utility"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], [
        "widget.json",
        "Cargo.toml",
        "src/lib.rs",
        "tests/test_my_widget.rs",
        "examples/example_usage.rs",
    ])
    # The Cargo package name must match the crate the test/example import.
    with open(f"{result['path']}/Cargo.toml") as f:
        assert 'name = "my_widget"' in f.read()
    with open(f"{result['path']}/tests/test_my_widget.rs") as f:
        assert "use my_widget::" in f.read()


def test_create_gdscript_widget(carto, tmp_path, monkeypatch):
    # GDScript ships supported=False until its CI provisions Godot; the scaffold
    # itself is testable regardless of the ship gate.
    from cartograph.languages.gdscript import GDScriptEngine
    monkeypatch.setattr(GDScriptEngine, "supported", True)
    result = carto.create(
        "my-widget",
        language="gdscript",
        name="My Widget",
        domain="gamedev",
        tags=["utility"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], [
        "widget.json",
        "project.godot",
        "src/my_widget.gd",
        "tests/test_my_widget.gd",
        "examples/example_usage.gd",
    ])
    # src must declare a PascalCase class_name; the test loads it via res://.
    with open(f"{result['path']}/src/my_widget.gd") as f:
        assert "class_name MyWidget" in f.read()
    with open(f"{result['path']}/tests/test_my_widget.gd") as f:
        assert 'res://src/my_widget.gd' in f.read()


def test_create_java_widget(carto, tmp_path, monkeypatch):
    # Java ships supported=False until its cross-platform CI proves the
    # Gradle toolchain; the scaffold itself is testable regardless.
    from cartograph.languages.java import JavaEngine
    monkeypatch.setattr(JavaEngine, "supported", True)
    result = carto.create(
        "my-widget",
        language="java",
        name="My Widget",
        domain="backend",
        tags=["utility"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], [
        "widget.json",
        "settings.gradle",
        "build.gradle",
        "cartograph-deps.gradle",
        "src/MyWidget.java",
        "tests/MyWidgetTest.java",
        "examples/ExampleUsage.java",
    ])
    # The source package must match what the test/example import.
    with open(f"{result['path']}/src/MyWidget.java") as f:
        assert "package my_widget;" in f.read()
    with open(f"{result['path']}/tests/MyWidgetTest.java") as f:
        assert "import my_widget.MyWidget;" in f.read()
    with open(f"{result['path']}/settings.gradle") as f:
        assert "rootProject.name = 'my_widget'" in f.read()


def test_create_csharp_widget(carto, tmp_path, monkeypatch):
    # C# ships supported=False until its cross-platform CI proves the
    # .NET toolchain; the scaffold itself is testable regardless.
    from cartograph.languages.csharp import CSharpEngine
    monkeypatch.setattr(CSharpEngine, "supported", True)
    result = carto.create(
        "my-widget",
        language="csharp",
        name="My Widget",
        domain="backend",
        tags=["utility"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], [
        "widget.json",
        "cartograph-deps.props",
        "src/my_widget.csproj",
        "src/MyWidget.cs",
        "tests/Tests.csproj",
        "tests/MyWidgetTests.cs",
        "examples/Examples.csproj",
        "examples/ExampleUsage.cs",
    ])
    # The source namespace must match what the test/example import.
    with open(f"{result['path']}/src/MyWidget.cs") as f:
        assert "namespace MyWidgetWidget;" in f.read()
    with open(f"{result['path']}/tests/MyWidgetTests.cs") as f:
        assert "using MyWidgetWidget;" in f.read()
    with open(f"{result['path']}/tests/Tests.csproj") as f:
        assert "../src/my_widget.csproj" in f.read()
    with open(f"{result['path']}/examples/ExampleUsage.cs") as f:
        assert "using MyWidgetWidget;" in f.read()


def test_create_nim_widget(carto, tmp_path):
    result = carto.create(
        "my-nim-widget",
        language="nim",
        name="My Nim Widget",
        domain="universal",
        tags=["utility"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], [
        "widget.json",
        "my_nim_widget.nimble",
        "src/my_nim_widget_lib.nim",
        "tests/test_my_nim_widget.nim",
        "examples/example_usage.nim",
    ])


def test_create_openscad_widget(carto, tmp_path):
    result = carto.create(
        "my-scad-widget",
        language="openscad",
        name="My Scad Widget",
        domain="modeling",
        tags=["utility"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], [
        "widget.json",
        "src/my_scad_widget.scad",
        "tests/test_my_scad_widget.scad",
        "examples/example_usage.scad",
        # OpenSCAD ships a python/ sidecar for the contamination fallback.
        "python/my_scad_widget.py",
        "python/test_my_scad_widget.py",
    ])


def test_create_systemverilog_widget(carto, tmp_path):
    result = carto.create(
        "my-sv-widget",
        language="systemverilog",
        name="My Sv Widget",
        domain="rtl",
        tags=["utility"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], [
        "widget.json",
        "src/my_sv_widget.sv",
        "tests/test_my_sv_widget.sv",
        "examples/example_usage.sv",
    ])


def test_create_typescript_widget(carto, tmp_path):
    result = carto.create(
        "my-ts-widget",
        language="typescript",
        name="My Ts Widget",
        domain="universal",
        tags=["utility"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], [
        "widget.json",
        "package.json",
        "vitest.config.js",
        "src/my_ts_widget.ts",
        "tests/test_my_ts_widget.ts",
        "examples/example_usage.ts",
    ])


def test_create_spice_widget(carto, tmp_path, monkeypatch):
    # SPICE ships supported=False until its stress test passes; the scaffold
    # itself is testable regardless of the ship gate.
    from cartograph.languages.spice import SpiceEngine
    monkeypatch.setattr(SpiceEngine, "supported", True)
    result = carto.create(
        "my-filter",
        language="spice",
        name="My Filter",
        domain="analog",
        tags=["filter"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], [
        "widget.json",
        "src/my_filter.cir",
        "tests/test_my_filter.cir",
        "examples/example_usage.cir",
    ])
    # src/ must be a reusable .subckt block; the testbench must include it
    # and assert a measured quantity.
    with open(f"{result['path']}/src/my_filter.cir") as f:
        assert ".subckt my_filter" in f.read()
    with open(f"{result['path']}/tests/test_my_filter.cir") as f:
        tb = f.read()
    assert ".include ../src/my_filter.cir" in tb
    assert "meas" in tb and "ASSERT_PASS" in tb


def test_create_rejects_leading_digit_name(carto, tmp_path):
    """A name that becomes a digit-leading identifier (illegal in every
    supported language) is rejected loudly, not silently mangled. Regression
    for the '8b10b-encoder' -> illegal 'module 8b10b_encoder' class of bug."""
    result = carto.create(
        "8b10b-encoder",
        language="systemverilog",
        domain="rtl",
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "error"
    assert "digit" in result["message"].lower()
    # Nothing should be left on disk when the name is rejected.
    assert not os.path.exists(os.path.join(str(tmp_path), "cg"))


def test_create_rejects_illegal_identifier_chars(carto, tmp_path):
    result = carto.create(
        "my widget",
        language="python",
        domain="backend",
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "error"
    assert "identifier" in result["message"].lower()


def test_create_accepts_reordered_name(carto, tmp_path):
    """The suggested reorder of a digit-leading name scaffolds cleanly."""
    result = carto.create(
        "encoder-8b10b",
        language="systemverilog",
        domain="rtl",
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], ["widget.json", "src", "tests"])


def test_create_lean_widget(carto, tmp_path, monkeypatch):
    # Lean ships supported=False until its cross-platform CI proves the
    # elan toolchain; the scaffold itself is testable regardless.
    from cartograph.languages.lean import LeanEngine
    monkeypatch.setattr(LeanEngine, "supported", True)
    result = carto.create(
        "my-widget",
        language="lean",
        name="My Widget",
        domain="formal",
        tags=["utility"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], [
        "widget.json",
        "lakefile.toml",
        "src/my_widget.lean",
        "tests/test_my_widget.lean",
        "examples/example_usage.lean",
    ])
    # The lake lib name must match the module the test/example import.
    with open(f"{result['path']}/lakefile.toml") as f:
        assert 'name = "my_widget"' in f.read()
    with open(f"{result['path']}/tests/test_my_widget.lean") as f:
        assert "import my_widget" in f.read()
    with open(f"{result['path']}/examples/example_usage.lean") as f:
        assert "import my_widget" in f.read()


def test_create_flutter_widget(carto, tmp_path, monkeypatch):
    # Flutter ships supported=False until its cross-platform CI proves the
    # SDK toolchain; the scaffold itself is testable regardless.
    from cartograph.languages.flutter import FlutterEngine
    monkeypatch.setattr(FlutterEngine, "supported", True)
    result = carto.create(
        "my-widget",
        language="flutter",
        name="My Widget",
        domain="frontend",
        tags=["utility"],
        target_dir=str(tmp_path),
    )
    assert result.get("status") == "success"
    _assert_scaffold(result["path"], [
        "widget.json",
        "pubspec.yaml",
        "analysis_options.yaml",
        "src/my_widget.dart",
        "tests/my_widget_test.dart",
        "examples/example_usage.dart",
    ])
    # The pubspec package name must match what the test/example import.
    with open(f"{result['path']}/pubspec.yaml") as f:
        pubspec = f.read()
    assert "name: my_widget" in pubspec
    # Both generated marker blocks must survive scaffolding - install_deps
    # and blueprint wiring regenerate the pubspec between them.
    assert "cartograph-deps start" in pubspec
    assert "cartograph-composed start" in pubspec
    with open(f"{result['path']}/tests/my_widget_test.dart") as f:
        assert "import 'package:my_widget/my_widget.dart';" in f.read()
    with open(f"{result['path']}/examples/example_usage.dart") as f:
        example = f.read()
    assert "import 'package:my_widget/my_widget.dart';" in example
    # Examples validate through the flutter_test harness - the scaffold
    # must ship at least one test block or `flutter test` finds no tests.
    assert "testWidgets(" in example
