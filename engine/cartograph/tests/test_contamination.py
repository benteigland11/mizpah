"""
Tests for the contamination and validation pipeline.

This is the most critical gate in widget quality. Every check that can block
or warn during checkin must be tested here.

Structure:
  1. Base capability tests - parameterized across all engines. These verify
     the 8 required contamination checks defined in base.py. If a language
     can't pass these, it doesn't ship.

  2. Language-specific tests - checks unique to each engine's validate_widget
     (print detection, echo detection, console.log, etc.).

  3. Shared validation tests - dep pinning, orchestrator delegation, base
     class regex fallback.
"""
import json
import os
import shutil
from unittest.mock import patch

import pytest

from cartograph.languages.python import PythonEngine
from cartograph.languages.javascript import JavaScriptEngine
from cartograph.languages.nim import NimEngine
from cartograph.languages.systemverilog import SystemVerilogEngine
from cartograph.languages.angular import AngularEngine
from cartograph.languages.php import PhpEngine
from cartograph.languages.terraform import TerraformEngine
from cartograph.languages.go import GoEngine
from cartograph.languages.rust import RustEngine
from cartograph.languages.gdscript import GDScriptEngine
from cartograph.languages.java import JavaEngine
from cartograph.languages.lean import LeanEngine
from cartograph.languages.csharp import CSharpEngine
from cartograph.languages.flutter import FlutterEngine
from cartograph.languages.spice import SpiceEngine
from cartograph.languages.base import LanguageEngine
from cartograph.contamination import scan_contamination


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _make_widget(tmp_path, language, src_filename, src_code,
                 test_code="", dependencies=None, example_code=""):
    """Create a minimal widget dir with the given source and return its path."""
    wdir = str(tmp_path)
    manifest = {
        "meta": {"id": "test-widget", "name": "Test Widget",
                 "version": "1.0.0", "tags": ["test"], "domain": "backend"},
        "description": "Test widget for contamination checks.",
        "tech_stack": {
            "language": language,
            "dependencies": dependencies or [],
        },
    }
    _write(os.path.join(wdir, "widget.json"), json.dumps(manifest, indent=2))
    _write(os.path.join(wdir, "src", src_filename), src_code)
    if language == "python":
        _write(os.path.join(wdir, "src", "__init__.py"), "")
    if test_code:
        test_name = "test_" + src_filename
        _write(os.path.join(wdir, "tests", test_name), test_code)
    if example_code:
        ext = os.path.splitext(src_filename)[1]
        _write(os.path.join(wdir, "examples", f"example_usage{ext}"), example_code)
    return wdir


# ---------------------------------------------------------------------------
# Per-language scan helpers
# ---------------------------------------------------------------------------

def _scan(tmp_path, language, ext, src_code, test_code="", dependencies=None,
          example_code=""):
    """Run scan_contamination for a given language engine."""
    engines = {
        "python": PythonEngine,
        "javascript": JavaScriptEngine,
        "nim": NimEngine,
        "systemverilog": SystemVerilogEngine,
        "angular": AngularEngine,
        "php": PhpEngine,
        "terraform": TerraformEngine,
        "go": GoEngine,
        "spice": SpiceEngine,
        "rust": RustEngine,
        "gdscript": GDScriptEngine,
        "java": JavaEngine,
        "lean": LeanEngine,
        "csharp": CSharpEngine,
        "flutter": FlutterEngine,
    }
    wdir = _make_widget(tmp_path, language, f"module.{ext}", src_code,
                        test_code, dependencies, example_code=example_code)
    engine = engines[language]()
    tech_stack = {"language": language, "dependencies": dependencies or []}
    return engine.scan_contamination(wdir, tech_stack)


# ---------------------------------------------------------------------------
# Test data: per-language source snippets for each contamination check
#
# Each entry: (src_code, test_code_or_None, dependencies_or_None)
# src_code is planted in src/, test_code in tests/ if provided.
# ---------------------------------------------------------------------------

# Noop source per language (clean code that triggers nothing)
CLEAN = {
    "python":     ("def hello():\n    return 'world'\n", "", None),
    "javascript": ("function hello() { return 'world' }\n", "", None),
    "nim":        ("proc hello*(): string =\n  \"world\"\n", "", None),
    "systemverilog": ("module clean #(parameter int W = 8)(\n"
                      "    input logic clk, input logic rst_n,\n"
                      "    input logic [W-1:0] d_in, output logic [W-1:0] d_out\n"
                      ");\n    always_ff @(posedge clk) begin\n"
                      "        if (!rst_n) d_out <= '0;\n"
                      "        else d_out <= d_in;\n"
                      "    end\nendmodule\n", "", None),
    "angular":    ("export class ItemComponent { getValue() { return 'world'; } }\n", "", None),
    "php":        ("<?php\nclass Item { public function getValue(): mixed { return 'world'; } }\n", "", None),
    "terraform":  ('resource "null_resource" "x" {\n  triggers = { name = var.name }\n}\n', "", None),
    "go":         ('package module\n\n// Hello returns a greeting.\nfunc Hello() string { return "world" }\n', "", None),
    "rust":       ('/// Hello returns a greeting.\npub fn hello() -> String { String::from("world") }\n', "", None),
    "java":       ('/** Clean module. */\npublic final class Module {\n    private Module() {}\n\n    /** Returns a greeting. */\n    public static String hello() { return "world"; }\n}\n', "", None),
    "gdscript":   ('func hello() -> String:\n\treturn "world"\n', "", None),
    "lean":       ('/-- Returns a greeting. -/\ndef hello : String := "world"\n', "", None),
    "csharp":     ('/// <summary>Clean module.</summary>\npublic static class Module\n{\n    /// <summary>Returns a greeting.</summary>\n    public static string Hello() { return "world"; }\n}\n', "", None),
    "flutter":    ("String hello() => 'world';\n", "", None),
}

# Check 1: Absolute paths in src/ -> block
ABS_PATH_SRC = {
    "python":     'LOG = "/home/user/logs/app.log"\n',
    "javascript": "const LOG = '/home/user/logs/app.log'\n",
    "nim":        'let logDir = "/home/user/logs/app"\n',
    "systemverilog": 'module m; localparam string P = "/home/user/data"; endmodule\n',
    "angular":    "const LOG = '/home/user/logs/app.log'\n",
    "php":        "<?php\n$log = '/home/user/logs/app.log';\n",
    "terraform":  'locals {\n  log = "/home/user/logs/app.log"\n}\n',
    "go":         'package module\n\nfunc LogPath() string { return "/home/user/logs/app.log" }\n',
    "rust":       'pub fn log_path() -> &\'static str { "/home/user/logs/app.log" }\n',
    "java":       'public final class Module {\n    public static String logPath() { return "/home/user/logs/app.log"; }\n}\n',
    "csharp":     'public static class Module\n{\n    public static string LogPath() { return "/home/user/logs/app.log"; }\n}\n',
    "flutter":    "String logPath() => '/home/user/logs/app.log';\n",
    "gdscript":   'var log_path: String = "/home/user/logs/app.log"\n',
    "lean":       'def logPath : String := "/home/user/logs/app.log"\n',
}

# Check 2: Credentials in src/ -> block
CREDENTIAL_SRC = {
    "python":     'api_key = "sk-abc123verylongkey"\n',
    "javascript": "const api_key = 'sk-abc123verylongkey'\n",
    "nim":        'let api_key = "sk-abc123verylongkey"\n',
    "systemverilog": 'module m;\nlocalparam string api_key = "sk-abc123verylongkey";\nendmodule\n',
    "angular":    "const api_key = 'sk-abc123verylongkey'\n",
    "php":        "<?php\n$api_key = 'sk-abc123verylongkey';\n",
    "terraform":  'locals {\n  api_key = "sk-abc123verylongkey"\n}\n',
    "go":         'package module\n\nfunc Key() string {\n\tapiKey := "sk-abc123verylongkey"\n\treturn apiKey\n}\n',
    "rust":       'pub fn key() -> String {\n    let api_key = "sk-abc123verylongkey";\n    api_key.to_string()\n}\n',
    "java":       'public final class Module {\n    public static String key() {\n        String api_key = "sk-abc123verylongkey";\n        return api_key;\n    }\n}\n',
    "csharp":     'public static class Module\n{\n    public static string Key()\n    {\n        string api_key = "sk-abc123verylongkey";\n        return api_key;\n    }\n}\n',
    "flutter":    "String key() {\n  final api_key = 'sk-abc123verylongkey';\n  return api_key;\n}\n",
    "gdscript":   'var api_key: String = "sk-abc123verylongkey"\n',
    "lean":       'def api_key := "sk-abc123verylongkey"\n',
}

# Check 2b: Credentials in tests/ -> warning (not block)
CREDENTIAL_TEST = {
    "python":     'password = "fake_test_password_123"\n',
    "javascript": "const password = 'fake_test_password_123'\n",
    "nim":        'let password = "fake_test_password_123"\n',
    "systemverilog": 'module m;\npassword = "fake_test_password_123";\nendmodule\n',
    "angular":    "const password = 'fake_test_password_123'\n",
    "php":        "<?php\n$password = 'fake_test_password_123';\n",
    "terraform":  'locals {\n  password = "fake_test_password_123"\n}\n',
    "go":         'package tests\n\nfunc fakeCred() string {\n\tpassword := "fake_test_password_123"\n\treturn password\n}\n',
    "rust":       'fn fake_cred() -> String {\n    let password = "fake_test_password_123";\n    password.to_string()\n}\n',
    "java":       'class ModuleTest {\n    String fakeCred() {\n        String password = "fake_test_password_123";\n        return password;\n    }\n}\n',
    "csharp":     'public class ModuleTests\n{\n    public string FakeCred()\n    {\n        string password = "fake_test_password_123";\n        return password;\n    }\n}\n',
    "flutter":    "String fakeCred() {\n  final password = 'fake_test_password_123';\n  return password;\n}\n",
    "gdscript":   'var password: String = "fake_test_password_123"\n',
    "lean":       'def password := "fake_test_password_123"\n',
}

# Check 3: Hardcoded URLs -> block
URL_SRC = {
    "python":     'API = "https://api.mycompany.com/v1"\n',
    "javascript": "const API = 'https://api.mycompany.com/v1'\n",
    "nim":        'let api = "https://api.mycompany.com/v1"\n',
    "systemverilog": 'module m; localparam string U = "https://api.mycompany.com/v1"; endmodule\n',
    "angular":    "const API = 'https://api.mycompany.com/v1'\n",
    "php":        "<?php\n$api = 'https://api.mycompany.com/v1';\n",
    "terraform":  'locals {\n  api = "https://api.mycompany.com/v1"\n}\n',
    "go":         'package module\n\nfunc API() string { return "https://api.mycompany.com/v1" }\n',
    "rust":       'pub fn api() -> &\'static str { "https://api.mycompany.com/v1" }\n',
    "java":       'public final class Module {\n    public static String api() { return "https://api.mycompany.com/v1"; }\n}\n',
    "csharp":     'public static class Module\n{\n    public static string Api() { return "https://api.mycompany.com/v1"; }\n}\n',
    "flutter":    "String api() => 'https://api.mycompany.com/v1';\n",
    "gdscript":   'var api: String = "https://api.mycompany.com/v1"\n',
    "lean":       'def api : String := "https://api.mycompany.com/v1"\n',
}

# Check 3b: localhost/example.com URLs -> allowed
URL_ALLOWED = {
    "python":     'API = "http://localhost:8080/api"\n',
    "javascript": "const API = 'http://localhost:8080/api'\n",
    "nim":        'let api = "http://localhost:8080/api"\n',
    "systemverilog": 'module m; localparam string U = "http://localhost:8080/api"; endmodule\n',
    "angular":    "const API = 'http://localhost:8080/api'\n",
    "php":        "<?php\n$api = 'http://localhost:8080/api';\n",
    "terraform":  'locals {\n  api = "http://localhost:8080/api"\n}\n',
    "go":         'package module\n\nfunc API() string { return "http://localhost:8080/api" }\n',
    "rust":       'pub fn api() -> &\'static str { "http://localhost:8080/api" }\n',
    "java":       'public final class Module {\n    public static String api() { return "http://localhost:8080/api"; }\n}\n',
    "csharp":     'public static class Module\n{\n    public static string Api() { return "http://localhost:8080/api"; }\n}\n',
    "flutter":    "String api() => 'http://localhost:8080/api';\n",
    "gdscript":   'var api: String = "http://localhost:8080/api"\n',
    "lean":       'def api : String := "http://localhost:8080/api"\n',
}

# Check 4: Hardcoded IPs -> block
IP_SRC = {
    "python":     'HOST = "192.168.1.100"\n',
    "javascript": "const HOST = '192.168.1.100'\n",
    "nim":        'let host = "192.168.1.100"\n',
    "systemverilog": 'module m; localparam string H = "192.168.1.100"; endmodule\n',
    "angular":    "const HOST = '192.168.1.100'\n",
    "php":        "<?php\n$host = '192.168.1.100';\n",
    "terraform":  'locals {\n  host = "192.168.1.100"\n}\n',
    "go":         'package module\n\nfunc Host() string { return "192.168.1.100" }\n',
    "rust":       'pub fn host() -> &\'static str { "192.168.1.100" }\n',
    "java":       'public final class Module {\n    public static String host() { return "192.168.1.100"; }\n}\n',
    "csharp":     'public static class Module\n{\n    public static string Host() { return "192.168.1.100"; }\n}\n',
    "flutter":    "String host() => '192.168.1.100';\n",
    "gdscript":   'var host: String = "192.168.1.100"\n',
    "lean":       'def host : String := "192.168.1.100"\n',
}

# Check 5: Sleep in src/ -> block
SLEEP_SRC = {
    "python":     "import time\ntime.sleep(1)\n",
    "javascript": "setTimeout(() => {}, 1000)\n",
    "nim":        "sleep(1000)\n",
    "angular":    "setTimeout(() => {}, 1000)\n",
    "php":        "<?php\nsleep(1);\n",
    "go":         'package module\n\nimport "time"\n\nfunc Wait() { time.Sleep(1 * time.Second) }\n',
    "java":       'public final class Module {\n    public static void pause() throws InterruptedException { Thread.sleep(1000); }\n}\n',
    "csharp":     'public static class Module\n{\n    public static void Pause() { Thread.Sleep(1000); }\n}\n',
    "flutter":    "import 'dart:io';\n\nvoid pause() {\n  sleep(const Duration(seconds: 1));\n}\n",
    "rust":       'use std::thread;\nuse std::time::Duration;\n\npub fn wait() { thread::sleep(Duration::from_secs(1)); }\n',
    "lean":       'def wait : IO Unit := IO.sleep 1000\n',
}

# Check 5b: Sleep in tests/ with small duration -> no warning
SLEEP_TEST_SMALL = {
    "python":     "import time\ntime.sleep(0.5)\n",
    "javascript": "setTimeout(() => {}, 500)\n",
    "nim":        "sleep(500)\n",
    "angular":    "setTimeout(() => {}, 500)\n",
    "php":        "<?php\nsleep(1);\n",  # 1 second - not > 1, no warning
    "go":         'package tests\n\nimport "time"\n\nfunc wait() { time.Sleep(500 * time.Millisecond) }\n',
    "java":       'class ModuleTest {\n    void pause() throws InterruptedException { Thread.sleep(500); }\n}\n',
    "csharp":     'public class ModuleTests\n{\n    public void Pause() { Thread.Sleep(500); }\n}\n',
    "flutter":    "import 'dart:io';\n\nvoid pause() {\n  sleep(const Duration(milliseconds: 500));\n}\n",
    "rust":       'use std::thread;\nuse std::time::Duration;\n\nfn wait() { thread::sleep(Duration::from_millis(500)); }\n',
    "lean":       'def wait : IO Unit := IO.sleep 500\n',
}

# Check 5c: Sleep in tests/ with large duration -> warning
SLEEP_TEST_LARGE = {
    "python":     "import time\ntime.sleep(5)\n",
    "javascript": "setTimeout(() => {}, 5000)\n",
    "nim":        "sleep(5000)\n",
    "angular":    "setTimeout(() => {}, 5000)\n",
    "php":        "<?php\nsleep(5);\n",
    "go":         'package tests\n\nimport "time"\n\nfunc wait() { time.Sleep(5 * time.Second) }\n',
    "java":       'class ModuleTest {\n    void pause() throws InterruptedException { Thread.sleep(5000); }\n}\n',
    "csharp":     'public class ModuleTests\n{\n    public void Pause() { Thread.Sleep(5000); }\n}\n',
    "flutter":    "import 'dart:io';\n\nvoid pause() {\n  sleep(const Duration(seconds: 5));\n}\n",
    "rust":       'use std::thread;\nuse std::time::Duration;\n\nfn wait() { thread::sleep(Duration::from_secs(5)); }\n',
    "lean":       'def wait : IO Unit := IO.sleep 5000\n',
}

# Check 6: Hardcoded values -> warning
HARDCODED_VALUE = {
    "python":     "TIMEOUT = 30\n",
    "javascript": "const TIMEOUT = 30\n",
    "nim":        "let timeout = 30\n",
    "angular":    "const TIMEOUT = 30\n",
    "php":        "<?php\nclass Item { private $TIMEOUT = 30; }\n",
    "go":         'package module\n\nconst Timeout = 30\n',
    "java":       'public final class Module {\n    static final int TIMEOUT = 30;\n    private Module() {}\n}\n',
    "csharp":     'public static class Module\n{\n    public const int Timeout = 30;\n}\n',
    "flutter":    "const int timeout = 30;\n",
    "rust":       'pub const TIMEOUT: u64 = 30;\n',
    "lean":       'def timeout := 30\n',
}

# Check 7: Env var access -> warning
ENV_VAR = {
    "python":     "import os\nv = os.getenv('KEY')\n",
    "javascript": "const v = process.env.KEY\n",
    "nim":        'let v = getEnv("KEY")\n',
    "angular":    "const v = process.env['KEY']\n",
    "php":        "<?php\n$v = getenv('KEY');\n",
    "go":         'package module\n\nimport "os"\n\nfunc Cfg() string { return os.Getenv("KEY") }\n',
    "java":       'public final class Module {\n    public static String cfg() { return System.getenv("KEY"); }\n}\n',
    "csharp":     'public static class Module\n{\n    public static string? Cfg() { return Environment.GetEnvironmentVariable("KEY"); }\n}\n',
    "flutter":    "import 'dart:io';\n\nString? cfg() => Platform.environment['KEY'];\n",
    "rust":       'pub fn cfg() -> Option<String> { std::env::var("KEY").ok() }\n',
    "lean":       'def cfg : IO (Option String) := IO.getEnv "KEY"\n',
}

# Check 8: Unlisted imports -> warning
UNLISTED_IMPORT = {
    "python":     "import requests\n",
    "javascript": "const axios = require('axios')\n",
    "nim":        "import somepkg\n",
    "angular":    "import { something } from 'some-unregistered-pkg'\n",
    "php":        "<?php\nuse GuzzleHttp\\Client;\n",
    "go":         'package module\n\nimport "github.com/google/uuid"\n\nvar _ = uuid.New\n',
    "java":       'import com.google.gson.Gson;\n\npublic final class Module {\n    public static String js(Object o) { return new Gson().toJson(o); }\n}\n',
    "csharp":     'using Newtonsoft.Json;\n\npublic static class Module\n{\n    public static string Js(object o) { return JsonConvert.SerializeObject(o); }\n}\n',
    "flutter":    "import 'package:http/http.dart' as http;\n\nString label(http.Client c) => c.toString();\n",
    "rust":       'use some_unregistered_crate::Thing;\n',
    "lean":       'import Somepkg\n',
}

# Check 8b: Listed imports -> no warning
LISTED_IMPORT = {
    "python":     ("import requests\n", ["requests>=2.0.0"]),
    "javascript": ("const axios = require('axios')\n", ["axios>=1.0.0"]),
    "nim":        ("import somepkg\n", ["somepkg>=1.0.0"]),
    "angular":    ("import { something } from 'some-registered-pkg'\n", ["some-registered-pkg>=1.0.0"]),
    "php":        ("<?php\nuse GuzzleHttp\\Client;\n", ["guzzlehttp/guzzle>=7.0.0"]),
    "go":         ('package module\n\nimport "github.com/google/uuid"\n\nvar _ = uuid.New\n', ["github.com/google/uuid>=1.6.0"]),
    "java":       ('import com.google.gson.Gson;\n\npublic final class Module {\n    public static String js(Object o) { return new Gson().toJson(o); }\n}\n', ["com.google.code.gson:gson>=2.11.0"]),
    "csharp":     ('using Newtonsoft.Json;\n\npublic static class Module\n{\n    public static string Js(object o) { return JsonConvert.SerializeObject(o); }\n}\n', ["Newtonsoft.Json>=13.0.3"]),
    "flutter":    ("import 'package:http/http.dart' as http;\n\nString label(http.Client c) => c.toString();\n", ["http>=1.2.0"]),
    "rust":       ('use some_registered_pkg::Thing;\n', ["some-registered-pkg>=1.0.0"]),
    "lean":       ('import Somepkg\n', ["Somepkg>=1.0.0"]),
}

# Check 8c: Stdlib imports -> no warning
STDLIB_IMPORT = {
    "python":     "import json\n",
    "javascript": "const path = require('path')\n",
    "nim":        "import std/json\n",
    "angular":    "import * as path from 'path'\n",
    # PHP builtins don't need use statements - calling strlen() has no import to flag
    "php":        "<?php\nclass Item { public function run(): void { strlen('test'); } }\n",
    "go":         'package module\n\nimport "encoding/json"\n\nvar _ = json.Marshal\n',
    "java":       'import java.util.List;\n\npublic final class Module {\n    public static int size(List<String> xs) { return xs.size(); }\n}\n',
    "csharp":     'using System.Text;\n\npublic static class Module\n{\n    public static int Size(StringBuilder b) { return b.Length; }\n}\n',
    "flutter":    "import 'dart:convert';\n\nString js(Object o) => jsonEncode(o);\n",
    "rust":       'use std::collections::HashMap;\n\npub fn f() -> HashMap<u8, u8> { HashMap::new() }\n',
    "lean":       'import Std\n',
}

# File extensions per language
EXT = {"python": "py", "javascript": "js", "nim": "nim", "systemverilog": "sv", "angular": "ts", "php": "php", "terraform": "tf", "go": "go", "rust": "rs", "gdscript": "gd", "java": "java", "lean": "lean", "csharp": "cs", "flutter": "dart"}

# Which languages need external tools to run their scanners
NEEDS_TOOL = {"javascript": "node", "nim": "nim", "systemverilog": "iverilog", "angular": "node", "go": "go", "rust": "rustc", "gdscript": "godot", "java": "java", "lean": "lean", "csharp": "dotnet", "flutter": "dart"}


# ---------------------------------------------------------------------------
# 1. BASE CAPABILITY TESTS - parameterized across all languages
# ---------------------------------------------------------------------------

# All languages with contamination engines
LANGUAGES = ["python", "javascript", "nim", "systemverilog", "angular", "php", "terraform", "go", "rust", "gdscript", "java", "lean", "csharp", "flutter"]

# Languages with native sleep/import/env detection (not applicable to SV)
LANGUAGES_SOFTWARE = ["python", "javascript", "nim", "angular", "php", "go", "rust", "java", "lean", "csharp", "flutter"]


def _skip_if_missing(lang):
    """Skip test if the language's external tool is not installed."""
    tool = NEEDS_TOOL.get(lang)
    if tool and not shutil.which(tool):
        pytest.skip(f"{tool} not installed")
    if lang == "java":
        # macOS ships a /usr/bin/java stub with no JDK behind it, so a PATH
        # hit isn't proof java can run - probe it like the engine does.
        from cartograph.languages import get_engine
        if get_engine("java").runtime_version() is None:
            pytest.skip("java on PATH is not a working JDK (macOS no-JDK stub)")
        # A JRE (or partial JDK) reports a version but lacks jdk.compiler,
        # which single-file source mode needs to run the native scanner.
        import subprocess
        try:
            mods = subprocess.run(["java", "--list-modules"],
                                  capture_output=True, text=True, timeout=30)
            if "jdk.compiler" not in (mods.stdout or ""):
                pytest.skip("java on PATH has no jdk.compiler module (JRE, "
                            "not a JDK) - scanner single-file mode unavailable")
        except Exception:
            pytest.skip("java on PATH could not be probed")
    if lang == "csharp":
        # The native scanner runs via file-based `dotnet run`, which needs
        # SDK 10+ - check_available probes the SDK major.
        from cartograph.languages import get_engine
        ok, msg = get_engine("csharp").check_available()
        if not ok:
            pytest.skip(f"csharp engine not ready: {msg}")


class TestContaminationStandard:
    """Every language engine must pass all 8 contamination checks.

    These tests are the contract defined in base.py's scan_contamination
    docstring. A new language engine is not ready until it passes all of these.
    """

    # -- Clean code --

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_clean_code_no_findings(self, tmp_path, lang):
        _skip_if_missing(lang)
        src, test, deps = CLEAN[lang]
        result = _scan(tmp_path, lang, EXT[lang], src, test, deps)
        assert result["blocks"] == [], f"{lang}: unexpected blocks: {result['blocks']}"
        assert result["warnings"] == [], f"{lang}: unexpected warnings: {result['warnings']}"

    # -- Check 1: Absolute paths -> block --

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_absolute_path_blocks(self, tmp_path, lang):
        _skip_if_missing(lang)
        result = _scan(tmp_path, lang, EXT[lang], ABS_PATH_SRC[lang])
        assert any("path" in b.lower() for b in result["blocks"]), \
            f"{lang}: absolute path not blocked: {result}"

    # -- Check 2: Credentials -> block in src, warn in tests --

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_credential_in_src_blocks(self, tmp_path, lang):
        _skip_if_missing(lang)
        result = _scan(tmp_path, lang, EXT[lang], CREDENTIAL_SRC[lang])
        assert any("credential" in b.lower() for b in result["blocks"]), \
            f"{lang}: credential not blocked: {result}"

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_credential_in_tests_warns_not_blocks(self, tmp_path, lang):
        _skip_if_missing(lang)
        clean_src = CLEAN[lang][0]
        result = _scan(tmp_path, lang, EXT[lang], clean_src,
                        test_code=CREDENTIAL_TEST[lang])
        assert not any("credential" in b.lower() for b in result["blocks"]), \
            f"{lang}: test credential should not block: {result['blocks']}"
        assert any("credential" in w.lower() for w in result["warnings"]), \
            f"{lang}: test credential should warn: {result['warnings']}"

    # -- Check 3: Hardcoded URLs -> block --

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_hardcoded_url_warns(self, tmp_path, lang):
        _skip_if_missing(lang)
        result = _scan(tmp_path, lang, EXT[lang], URL_SRC[lang])
        assert any("url" in w.lower() for w in result["warnings"]), \
            f"{lang}: hardcoded URL not warned: {result}"

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_localhost_url_allowed(self, tmp_path, lang):
        _skip_if_missing(lang)
        result = _scan(tmp_path, lang, EXT[lang], URL_ALLOWED[lang])
        assert not any("url" in b.lower() for b in result["blocks"]), \
            f"{lang}: localhost URL should not block: {result['blocks']}"

    # -- Check 4: Hardcoded IPs -> block --

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_hardcoded_ip_blocks(self, tmp_path, lang):
        _skip_if_missing(lang)
        result = _scan(tmp_path, lang, EXT[lang], IP_SRC[lang])
        assert any("ip" in b.lower() for b in result["blocks"]), \
            f"{lang}: hardcoded IP not blocked: {result}"

    # -- Check 5: Sleep/blocking -> block in src, conditional warn in tests --
    # (Not applicable to SystemVerilog - it has its own timing rules)

    @pytest.mark.parametrize("lang", LANGUAGES_SOFTWARE)
    def test_sleep_in_src_blocks(self, tmp_path, lang):
        _skip_if_missing(lang)
        result = _scan(tmp_path, lang, EXT[lang], SLEEP_SRC[lang])
        assert any("sleep" in b.lower() for b in result["blocks"]), \
            f"{lang}: sleep not blocked in src: {result}"

    @pytest.mark.parametrize("lang", LANGUAGES_SOFTWARE)
    def test_sleep_in_tests_small_no_warning(self, tmp_path, lang):
        _skip_if_missing(lang)
        clean_src = CLEAN[lang][0]
        result = _scan(tmp_path, lang, EXT[lang], clean_src,
                        test_code=SLEEP_TEST_SMALL[lang])
        assert not any("sleep" in w.lower() for w in result["warnings"]), \
            f"{lang}: small sleep should not warn: {result['warnings']}"

    @pytest.mark.parametrize("lang", LANGUAGES_SOFTWARE)
    def test_sleep_in_tests_large_warns(self, tmp_path, lang):
        _skip_if_missing(lang)
        clean_src = CLEAN[lang][0]
        result = _scan(tmp_path, lang, EXT[lang], clean_src,
                        test_code=SLEEP_TEST_LARGE[lang])
        assert any("sleep" in w.lower() for w in result["warnings"]), \
            f"{lang}: large sleep should warn: {result['warnings']}"

    # -- Check 6: Hardcoded values -> warning --
    # (Not applicable to SystemVerilog - it has its own hardcoded constant rules)

    @pytest.mark.parametrize("lang", LANGUAGES_SOFTWARE)
    def test_hardcoded_value_warns(self, tmp_path, lang):
        _skip_if_missing(lang)
        result = _scan(tmp_path, lang, EXT[lang], HARDCODED_VALUE[lang])
        assert any("hardcoded" in w.lower() for w in result["warnings"]), \
            f"{lang}: hardcoded value should warn: {result['warnings']}"

    # -- Check 7: Env var access -> warning --
    # (Not applicable to SystemVerilog - no env vars in HDL)

    @pytest.mark.parametrize("lang", LANGUAGES_SOFTWARE)
    def test_env_var_warns(self, tmp_path, lang):
        _skip_if_missing(lang)
        result = _scan(tmp_path, lang, EXT[lang], ENV_VAR[lang])
        assert any("env" in w.lower() for w in result["warnings"]), \
            f"{lang}: env var should warn: {result['warnings']}"

    # -- Check 8: Unlisted imports -> warning --
    # (Not applicable to SystemVerilog - no package imports in the same sense)

    @pytest.mark.parametrize("lang", LANGUAGES_SOFTWARE)
    def test_unlisted_import_flagged(self, tmp_path, lang):
        """Unlisted imports must be flagged (block for python, warn for others)."""
        _skip_if_missing(lang)
        result = _scan(tmp_path, lang, EXT[lang], UNLISTED_IMPORT[lang])
        all_findings = result["warnings"] + result["blocks"]
        assert any("unlisted" in f.lower() or "import" in f.lower()
                    for f in all_findings), \
            f"{lang}: unlisted import should be flagged: {result}"

    @pytest.mark.parametrize("lang", LANGUAGES_SOFTWARE)
    def test_listed_import_no_warning(self, tmp_path, lang):
        _skip_if_missing(lang)
        src, deps = LISTED_IMPORT[lang]
        result = _scan(tmp_path, lang, EXT[lang], src, dependencies=deps)
        assert not any("unlisted" in w.lower() for w in result["warnings"]), \
            f"{lang}: listed import should not warn: {result['warnings']}"

    @pytest.mark.parametrize("lang", LANGUAGES_SOFTWARE)
    def test_stdlib_import_no_warning(self, tmp_path, lang):
        _skip_if_missing(lang)
        result = _scan(tmp_path, lang, EXT[lang], STDLIB_IMPORT[lang])
        assert not any("unlisted" in w.lower() for w in result["warnings"]), \
            f"{lang}: stdlib import should not warn: {result['warnings']}"


# ---------------------------------------------------------------------------
# 2. LANGUAGE-SPECIFIC TESTS - checks unique to each engine
# ---------------------------------------------------------------------------

@pytest.mark.python
class TestPythonSpecific:
    """Python-only validation and contamination checks."""

    # -- validate_widget --

    def test_print_in_src_fails(self, tmp_path):
        wdir = _make_widget(tmp_path, "python", "mod.py", "print('debug')\n")
        result = PythonEngine().validate_widget(wdir, [])
        assert result["passed"] is False
        assert "print()" in result["error"]

    def test_missing_init_fails(self, tmp_path):
        wdir = _make_widget(tmp_path, "python", "mod.py", "def f(): pass\n")
        os.remove(os.path.join(wdir, "src", "__init__.py"))
        result = PythonEngine().validate_widget(wdir, [])
        assert result["passed"] is False
        assert "__init__.py" in result["error"]

    def test_clean_passes(self, tmp_path):
        wdir = _make_widget(tmp_path, "python", "mod.py", "def f(): pass\n")
        result = PythonEngine().validate_widget(wdir, [])
        assert result["passed"] is True

    # -- contamination extras --

    def test_asyncio_sleep_in_src_blocks(self, tmp_path):
        result = _scan(tmp_path, "python", "py",
                        "import asyncio\nasync def f():\n    await asyncio.sleep(1)\n")
        assert any("sleep" in b.lower() for b in result["blocks"])

    def test_from_import_sleep_in_src_blocks(self, tmp_path):
        result = _scan(tmp_path, "python", "py",
                        "from time import sleep\nsleep(5)\n")
        assert any("sleep" in b.lower() for b in result["blocks"])

    def test_from_import_sleep_aliased_blocks(self, tmp_path):
        result = _scan(tmp_path, "python", "py",
                        "from time import sleep as nap\nnap(5)\n")
        assert any("sleep" in b.lower() for b in result["blocks"])

    def test_from_import_sleep_in_tests_large_warns(self, tmp_path):
        result = _scan(tmp_path, "python", "py",
                        "def f(): pass\n",
                        test_code="from time import sleep\nsleep(5)\n")
        assert any("sleep" in w.lower() for w in result["warnings"])

    def test_absolute_path_windows_blocks(self, tmp_path):
        result = _scan(tmp_path, "python", "py",
                        'LOG = "C:\\\\Users\\\\dev\\\\logs"\n')
        assert any("path" in b.lower() for b in result["blocks"])

    def test_absolute_path_root_blocks(self, tmp_path):
        result = _scan(tmp_path, "python", "py",
                        'CFG = "/root/.config/myapp"\n')
        assert any("path" in b.lower() for b in result["blocks"])

    def test_ip_with_port_blocks(self, tmp_path):
        result = _scan(tmp_path, "python", "py", 'HOST = "10.0.0.5:8080"\n')
        assert any("IP" in b for b in result["blocks"])

    def test_example_com_url_allowed(self, tmp_path):
        result = _scan(tmp_path, "python", "py",
                        'API = "https://example.com/test"\n')
        assert not any("URL" in b for b in result["blocks"])

    def test_os_environ_warns(self, tmp_path):
        result = _scan(tmp_path, "python", "py",
                        "import os\nv = os.environ['KEY']\n")
        assert any("environ" in w for w in result["warnings"])

    def test_hardcoded_string_warns(self, tmp_path):
        result = _scan(tmp_path, "python", "py", 'MODEL = "gpt-4"\n')
        assert any("Hardcoded value" in w for w in result["warnings"])

    def test_password_in_src_blocks(self, tmp_path):
        result = _scan(tmp_path, "python", "py",
                        'password = "supersecretpassword"\n')
        assert any("credential" in b.lower() for b in result["blocks"])

    def test_hardcoded_url_in_test_file_not_warned(self, tmp_path):
        """Test files legitimately use mock URLs. URL warnings should be
        src/ only - matches hardcoded_value precedent."""
        result = _scan(tmp_path, "python", "py",
                       "def f(): pass\n",
                       test_code='URL = "https://api.realsite.com/v1"\n')
        assert not any("URL" in w for w in result["warnings"]), \
            f"hardcoded URLs in test files should not warn: {result['warnings']}"

    def test_hardcoded_ip_in_test_file_not_warned(self, tmp_path):
        """Test files legitimately use mock IPs as fixture data. IP warnings
        should be src/ only - matches hardcoded_url/hardcoded_value precedent
        and Nim/JS scanner behavior."""
        result = _scan(tmp_path, "python", "py",
                       "def f(): pass\n",
                       test_code='HOST = "10.0.0.1:8080"\n'
                                 'ADDR = "192.168.1.100"\n')
        ip_findings = [w for w in result["warnings"] + result["blocks"]
                       if "IP" in w]
        assert not ip_findings, \
            f"hardcoded IPs in test files should not warn: {result}"

    def test_abs_path_in_test_file_blocks(self, tmp_path):
        """Tests should use tmp_path fixtures, not real absolute home paths.
        abs_path must block in tests too, matching the Nim and JS scanners."""
        result = _scan(tmp_path, "python", "py",
                       "def f(): pass\n",
                       test_code='LOG = "/home/user/logs/app.log"\n')
        assert any("path" in b.lower() for b in result["blocks"]), \
            f"abs_path in test files must block: {result}"

    def test_unlisted_import_in_test_warns(self, tmp_path):
        """Tests must only use stdlib, declared deps, or local src modules.
        A third-party import in a test warns but does not block - tests are
        not shipped to users, so a missing dep here is diagnostic only."""
        result = _scan(tmp_path, "python", "py",
                       "def f(): return 1\n",
                       test_code="import flask\ndef test_f(): assert True\n")
        assert any("flask" in w for w in result["warnings"]), \
            f"unlisted import in test must warn: {result['warnings']}"
        assert not any("flask" in b for b in result["blocks"]), \
            f"unlisted import in test must not block: {result['blocks']}"

    def test_unlisted_import_in_example_warns(self, tmp_path):
        """Examples must only use stdlib, declared deps, or local src modules.
        A third-party import in an example warns but does not block."""
        result = _scan(tmp_path, "python", "py",
                       "def f(): return 1\n",
                       example_code="import requests\nprint(requests.__name__)\n")
        assert any("requests" in w for w in result["warnings"]), \
            f"unlisted import in example must warn: {result['warnings']}"
        assert not any("requests" in b for b in result["blocks"]), \
            f"unlisted import in example must not block: {result['blocks']}"

    def test_local_src_import_in_test_not_warned(self, tmp_path):
        """Test importing a local src/ module must not warn as unlisted."""
        result = _scan(tmp_path, "python", "py",
                       "def greet(): return 'hi'\n",
                       test_code="from src.module import greet\ndef test_f(): assert greet()\n")
        unlisted = [w for w in result["warnings"]
                    if "Unlisted" in w and "module" in w]
        assert not unlisted, \
            f"local src import in test must not warn: {result['warnings']}"

    def test_pytest_import_in_test_not_warned(self, tmp_path):
        """Test frameworks like pytest must not trigger unlisted import warnings."""
        result = _scan(tmp_path, "python", "py",
                       "def f(): return 1\n",
                       test_code="import pytest\ndef test_f(): assert True\n")
        unlisted = [w for w in result["warnings"] if "Unlisted" in w and "pytest" in w]
        assert not unlisted, \
            f"pytest import in test must not warn: {result['warnings']}"

    def test_hardcoded_value_in_example_not_warned(self, tmp_path):
        """Examples legitimately use hardcoded values as demo data - these
        must NOT be flagged (parity with tests)."""
        result = _scan(tmp_path, "python", "py",
                       "def f(): return 1\n",
                       example_code="G = 6.674e-11\nprint(G)\n")
        assert not any("Hardcoded" in w for w in result["warnings"]), \
            f"hardcoded values in examples must not warn: {result['warnings']}"


@pytest.mark.javascript
class TestJSSpecific:
    """JavaScript-only validation and contamination checks."""

    @pytest.fixture(autouse=True)
    def _require_node(self):
        if not shutil.which("node"):
            pytest.skip("Node.js not installed")

    # -- validate_widget --

    def test_console_log_in_src_fails(self, tmp_path):
        wdir = _make_widget(tmp_path, "javascript", "mod.js",
                            "console.log('debug')\n")
        result = JavaScriptEngine().validate_widget(wdir, [])
        assert result["passed"] is False
        assert "console" in result["error"].lower()

    def test_process_exit_in_src_fails(self, tmp_path):
        wdir = _make_widget(tmp_path, "javascript", "mod.js",
                            "process.exit(1)\n")
        result = JavaScriptEngine().validate_widget(wdir, [])
        assert result["passed"] is False
        assert "process.exit" in result["error"]

    def test_eval_in_src_fails(self, tmp_path):
        wdir = _make_widget(tmp_path, "javascript", "mod.js",
                            "eval('alert(1)')\n")
        result = JavaScriptEngine().validate_widget(wdir, [])
        assert result["passed"] is False
        assert "eval" in result["error"].lower()

    def test_clean_passes(self, tmp_path):
        wdir = _make_widget(tmp_path, "javascript", "mod.js",
                            "function hello() { return 1 }\n")
        result = JavaScriptEngine().validate_widget(wdir, [])
        assert result["passed"] is True

    # -- contamination extras --

    def test_setinterval_in_src_blocks(self, tmp_path):
        result = _scan(tmp_path, "javascript", "js",
                        "setInterval(() => {}, 1000)\n")
        assert any("sleep" in b.lower() or "setInterval" in b
                    for b in result["blocks"])

    def test_builtin_import_no_warning(self, tmp_path):
        result = _scan(tmp_path, "javascript", "js",
                        "const path = require('path')\n")
        assert not any("unlisted" in w.lower() for w in result["warnings"])

    def test_vitest_import_in_test_not_flagged(self, tmp_path):
        """Regression: vitest is the mandated JS test framework. Declared
        in package.json devDependencies, never widget.json. Must not flag
        as unlisted in test files - forcing an override every checkin is
        bad UX and conflicts with our own validation requirement."""
        result = _scan(tmp_path, "javascript", "js",
                       "export function f() { return 1 }\n",
                       test_code="import { test, expect } from 'vitest'\n"
                                 "test('x', () => expect(1).toBe(1))\n")
        assert not any("vitest" in w.lower() for w in result["warnings"]), \
            f"vitest must not warn in tests: {result['warnings']}"
        assert not any("vitest" in b.lower() for b in result["blocks"]), \
            f"vitest must not block in tests: {result['blocks']}"

    def test_jest_import_in_test_not_flagged(self, tmp_path):
        """Same as vitest - jest is a canonical test framework."""
        result = _scan(tmp_path, "javascript", "js",
                       "export function f() { return 1 }\n",
                       test_code="import { describe, it } from '@jest/globals'\n"
                                 "describe('x', () => it('y', () => {}))\n")
        assert not any("jest" in w.lower() for w in result["warnings"]), \
            f"jest must not warn in tests: {result['warnings']}"

    def test_test_framework_in_src_still_flagged(self, tmp_path):
        """Allowlist applies to tests/examples only. If someone imports
        vitest from src/, it's a real bug - block it."""
        result = _scan(tmp_path, "javascript", "js",
                       "import { test } from 'vitest'\n"
                       "export function f() { return 1 }\n")
        assert any("vitest" in b.lower() for b in result["blocks"]), \
            f"vitest in src/ must still block: {result}"

    def test_multiline_require_blocks_unlisted(self, tmp_path):
        result = _scan(
            tmp_path, "javascript", "js",
            "const axios = require(\n  'axios'\n)\n"
        )
        assert any("unlisted" in b.lower() for b in result["blocks"])

    def test_multiline_import_blocks_unlisted(self, tmp_path):
        result = _scan(
            tmp_path, "javascript", "js",
            "import {\n  thing\n} from 'axios'\n"
        )
        assert any("unlisted" in b.lower() for b in result["blocks"])

    def test_multiline_settimeout_in_src_blocks(self, tmp_path):
        result = _scan(
            tmp_path, "javascript", "js",
            "setTimeout(\n  () => {},\n  1000\n)\n"
        )
        assert any("sleep" in b.lower() for b in result["blocks"])

    def test_hardcoded_url_in_test_file_not_warned(self, tmp_path):
        """Test files legitimately use mock URLs. URL warnings should be
        src/ only - matches hardcoded_value precedent."""
        result = _scan(tmp_path, "javascript", "js",
                       "function f() { return 1 }\n",
                       test_code="const url = 'https://api.realsite.com/v1'\n")
        assert not any("url" in w.lower() for w in result["warnings"]), \
            f"hardcoded URLs in test files should not warn: {result['warnings']}"

    def test_unlisted_import_in_example_warns(self, tmp_path):
        """Examples must only use stdlib, declared deps, or local src
        modules. A third-party import in an example without declaration
        should warn."""
        result = _scan(tmp_path, "javascript", "js",
                       "export function f() { return 1 }\n",
                       example_code="const axios = require('axios')\nconsole.log(axios)\n")
        assert any("axios" in w.lower() and "unlisted" in w.lower()
                   for w in result["warnings"]), \
            f"unlisted import in example must warn: {result['warnings']}"

    def test_hardcoded_value_in_example_not_warned(self, tmp_path):
        """Examples use demo config values as fixtures."""
        result = _scan(tmp_path, "javascript", "js",
                       "export function f() { return 1 }\n",
                       example_code="const API_URL = 'https://api.demo.com'\nconst TIMEOUT = 5000\n")
        hv = [w for w in result["warnings"] if "consider making this a parameter" in w]
        assert not hv, \
            f"hardcoded values in examples must not warn: {hv}"


@pytest.mark.nim
class TestNimSpecific:
    """Nim-only validation and contamination checks."""

    @pytest.fixture(autouse=True)
    def _require_nim(self):
        if not shutil.which("nim"):
            pytest.skip("Nim not installed")

    # -- validate_widget (nim check + compile check + scanner errors) --

    def test_echo_in_src_fails(self, tmp_path):
        wdir = _make_widget(tmp_path, "nim", "mod.nim",
                            'echo "debug"\n')
        engine = NimEngine()
        result = engine.validate_widget(wdir, [])
        assert result["passed"] is False
        assert "echo" in result["error"].lower()

    def test_quit_in_src_fails(self, tmp_path):
        wdir = _make_widget(tmp_path, "nim", "mod.nim", "quit(1)\n")
        engine = NimEngine()
        result = engine.validate_widget(wdir, [])
        assert result["passed"] is False
        assert "quit" in result["error"].lower()

    def test_when_is_main_module_fails(self, tmp_path):
        wdir = _make_widget(tmp_path, "nim", "mod.nim",
                            'proc f() = discard\nwhen isMainModule:\n  f()\n')
        engine = NimEngine()
        result = engine.validate_widget(wdir, [])
        assert result["passed"] is False
        assert "isMainModule" in result["error"] or "main_module" in result["error"].lower()

    def test_clean_passes(self, tmp_path):
        wdir = _make_widget(tmp_path, "nim", "mod.nim",
                            "proc hello*(): string =\n  \"world\"\n")
        engine = NimEngine()
        result = engine.validate_widget(wdir, [])
        assert result["passed"] is True

    # -- contamination extras --

    def test_sleep_async_in_src_blocks(self, tmp_path):
        result = _scan(tmp_path, "nim", "nim",
                        "import std/asyncdispatch\nsleepAsync(1000)\n")
        assert any("sleep" in b.lower() for b in result["blocks"])

    def test_global_pragma_fails(self, tmp_path):
        wdir = _make_widget(tmp_path, "nim", "mod.nim",
                            'var x {.global.} = 0\n')
        engine = NimEngine()
        result = engine.validate_widget(wdir, [])
        assert result["passed"] is False

    def test_os_specific_when_defined_fails(self, tmp_path):
        wdir = _make_widget(tmp_path, "nim", "mod.nim",
                            'when defined(windows):\n  discard\n')
        engine = NimEngine()
        result = engine.validate_widget(wdir, [])
        assert result["passed"] is False

    def test_old_style_stdlib_import_warns(self, tmp_path):
        result = _scan(tmp_path, "nim", "nim", "import strutils\n")
        assert any("std/" in w.lower() or "modern nim" in w.lower() or "old-style" in w.lower()
                   for w in result["warnings"])

    def test_top_level_var_warns(self, tmp_path):
        result = _scan(tmp_path, "nim", "nim", "var cache = 0\nproc hello*(): string =\n  \"ok\"\n")
        assert any("top-level mutable state" in w.lower() or "top-level" in w.lower()
                   for w in result["warnings"])

    def test_var_inside_multiline_proc_signature_not_warned(self, tmp_path):
        # Regression: the scanner used to flip topLevelSection back to true
        # on the closing `) =` of a multi-line proc signature, causing the
        # first `var` of the proc body to be misreported as module-level.
        src = (
            "proc append(\n"
            "    a: var seq[int],\n"
            "    b: var int,\n"
            "    c: int\n"
            ") =\n"
            "  var current: seq[int] = @[]\n"
            "  current.add c\n"
            "\n"
            "proc rewrap(\n"
            "    items: seq[int]\n"
            "): tuple[x: int, y: int] =\n"
            "  var allRows: seq[int] = @[]\n"
            "  result = (items.len, allRows.len)\n"
        )
        result = _scan(tmp_path, "nim", "nim", src)
        tlv = [w for w in result["warnings"] if "top-level" in w.lower()]
        assert not tlv, f"locals inside multi-line proc signatures must not warn: {tlv}"

    def test_cast_seq_blocks_in_src(self, tmp_path):
        # GC-managed seq cast is a memory-safety hazard — block in src/.
        src = ("proc danger*(s: string): seq[byte] =\n"
               "  result = cast[seq[byte]](s)\n")
        result = _scan(tmp_path, "nim", "nim", src)
        assert any("seq" in b.lower() and "memory-safety" in b.lower()
                   for b in result["blocks"]), \
            f"cast[seq[T]] in src must block: {result}"

    def test_cast_seq_in_tests_warns(self, tmp_path):
        # Tests/examples can use cast[seq[T]] (still discouraged) — warn only.
        clean_src = "func hello*(): string = \"ok\"\n"
        test_code = ("import std/unittest\n"
                     "suite \"x\":\n"
                     "  test \"y\":\n"
                     "    let s = \"abc\"\n"
                     "    let b = cast[seq[byte]](s)\n"
                     "    check b.len == 3\n")
        result = _scan(tmp_path, "nim", "nim", clean_src, test_code=test_code)
        assert not any("memory-safety" in b.lower() for b in result["blocks"]), \
            f"cast[seq[T]] in tests must not block: {result['blocks']}"
        assert any("memory-safety" in w.lower() for w in result["warnings"]), \
            f"cast[seq[T]] in tests must warn: {result['warnings']}"

    def test_bare_except_warns(self, tmp_path):
        # Bare except: catches Defect/KeyboardInterrupt — warn.
        src = ("import std/strutils\n"
               "proc swallow*() =\n"
               "  try:\n"
               "    discard parseInt(\"x\")\n"
               "  except:\n"
               "    discard\n")
        result = _scan(tmp_path, "nim", "nim", src)
        assert any("bare" in w.lower() and "except" in w.lower()
                   for w in result["warnings"]), \
            f"bare `except:` must warn: {result['warnings']}"

    def test_typed_except_not_warned(self, tmp_path):
        # except CatchableError: is the recommended form — must not warn.
        src = ("import std/strutils\n"
               "proc safe*() =\n"
               "  try:\n"
               "    discard parseInt(\"x\")\n"
               "  except ValueError:\n"
               "    discard\n")
        result = _scan(tmp_path, "nim", "nim", src)
        assert not any("bare" in w.lower() and "except" in w.lower()
                       for w in result["warnings"]), \
            f"typed `except ValueError:` must not warn: {result['warnings']}"

    def test_raw_memory_alloc_warns(self, tmp_path):
        src = ("proc allocBuf*(n: int): pointer =\n"
               "  result = alloc(n)\n"
               "proc freeBuf*(p: pointer) =\n"
               "  dealloc(p)\n")
        result = _scan(tmp_path, "nim", "nim", src)
        warnings = [w for w in result["warnings"] if "raw memory" in w.lower()
                    or "alloc" in w.lower() or "dealloc" in w.lower()]
        assert warnings, f"alloc/dealloc must warn: {result['warnings']}"

    def test_raw_memory_cast_ptr_warns(self, tmp_path):
        src = ("proc badCast*(x: pointer): ptr int =\n"
               "  result = cast[ptr int](x)\n")
        result = _scan(tmp_path, "nim", "nim", src)
        assert any("raw" in w.lower() or "pointer" in w.lower()
                   for w in result["warnings"]), \
            f"cast[ptr ...] must warn: {result['warnings']}"

    def test_raw_memory_unchecked_array_warns(self, tmp_path):
        src = ("proc view*(p: pointer, n: int): ptr UncheckedArray[byte] =\n"
               "  result = cast[ptr UncheckedArray[byte]](p)\n")
        result = _scan(tmp_path, "nim", "nim", src)
        assert any("uncheckedarray" in w.lower() or "raw" in w.lower()
                   for w in result["warnings"]), \
            f"ptr UncheckedArray must warn: {result['warnings']}"

    def test_allocator_identifier_not_flagged_as_alloc(self, tmp_path):
        # Word-boundary check: `allocator` should not match `alloc`.
        src = ("proc make*(): string =\n"
               "  let allocator = \"name\"\n"
               "  result = allocator\n")
        result = _scan(tmp_path, "nim", "nim", src)
        assert not any("raw memory" in w.lower() or
                       (w.lower().startswith("alloc") and "primitive" in w.lower())
                       for w in result["warnings"]), \
            f"identifier `allocator` must not match alloc: {result['warnings']}"

    def test_hardcoded_value_in_test_file_not_warned(self, tmp_path):
        """Test files legitimately have expected values and fixtures -
        hardcoded value warnings should be src/ only."""
        clean_src = "proc hello*(): string =\n  \"ok\"\n"
        test_code = ("import std/unittest\n"
                     "let expected = 42\n"
                     "const FIXTURE = \"abc\"\n"
                     "suite \"x\":\n  test \"y\":\n    check 1 == 1\n")
        result = _scan(tmp_path, "nim", "nim", clean_src, test_code=test_code)
        assert not any("hardcoded" in w.lower() for w in result["warnings"]), \
            f"hardcoded values in test files should not warn: {result['warnings']}"

    def test_hardcoded_url_in_test_file_not_warned(self, tmp_path):
        """Test files often use mock URLs - URL warnings should be src/ only."""
        clean_src = "proc hello*(): string =\n  \"ok\"\n"
        test_code = ('import std/unittest\n'
                     'let url = "https://api.mocksite.com/v1"\n'
                     'suite "x":\n  test "y":\n    check 1 == 1\n')
        result = _scan(tmp_path, "nim", "nim", clean_src, test_code=test_code)
        assert not any("hardcoded url" in w.lower() for w in result["warnings"]), \
            f"hardcoded URLs in test files should not warn: {result['warnings']}"

    def test_hardcoded_ip_in_test_file_not_warned(self, tmp_path):
        """Test files legitimately use mock IPs as fixture data - IP warnings
        should be src/ only. Regression for infra-websocket-server-nim which
        was flagged for IPs hardcoded in its test fixtures."""
        clean_src = "proc hello*(): string =\n  \"ok\"\n"
        test_code = ('import std/unittest\n'
                     'let host = "10.0.0.1"\n'
                     'let addr = "192.168.1.100:8080"\n'
                     'suite "x":\n  test "y":\n    check 1 == 1\n')
        result = _scan(tmp_path, "nim", "nim", clean_src, test_code=test_code)
        ip_findings = [w for w in result["warnings"] + result["blocks"]
                       if "hardcoded ip" in w.lower()]
        assert not ip_findings, \
            f"hardcoded IPs in test files should not warn: {result}"

    def test_unlisted_import_in_example_warns(self, tmp_path):
        """Examples must only use stdlib, declared deps, or local src
        modules. A third-party import in an example without declaration
        should warn."""
        result = _scan(tmp_path, "nim", "nim",
                       "func hello*(): string = \"ok\"\n",
                       example_code="import chronos\nproc main() = discard\nmain()\n")
        assert any("chronos" in w and "unlisted" in w.lower()
                   for w in result["warnings"]), \
            f"unlisted import in example must warn: {result['warnings']}"

    def test_hardcoded_value_in_example_not_warned(self, tmp_path):
        """Examples legitimately use hardcoded values as demo/physical
        constants. Regression for physics-two-body-barycenter-nim which
        has gravitational constants in its example."""
        result = _scan(tmp_path, "nim", "nim",
                       "func hello*(): string = \"ok\"\n",
                       example_code="let mEarth = 5.972e24\n"
                                    "let mMoon = 7.342e22\n"
                                    "echo mEarth + mMoon\n")
        hv = [w for w in result["warnings"] if "parameter" in w.lower()]
        assert not hv, \
            f"hardcoded values in examples must not warn: {hv}"

    def test_top_level_var_in_example_not_warned(self, tmp_path):
        """Examples are scripts - top-level var is natural and expected."""
        result = _scan(tmp_path, "nim", "nim",
                       "func hello*(): string = \"ok\"\n",
                       example_code="var counter = 0\ncounter.inc\necho counter\n")
        tlv = [w for w in result["warnings"] if "top-level" in w.lower()]
        assert not tlv, \
            f"top-level vars in examples must not warn: {tlv}"

    def test_local_src_import_in_example_not_warned(self, tmp_path):
        """Examples importing a local widget src/ module must not warn as
        unlisted - the scanner allowlists filenames found in src/."""
        src_code = "func double*(x: int): int = x * 2\n"
        wdir = _make_widget(tmp_path, "nim", "double_lib.nim", src_code,
                            example_code="import double_lib\necho double(21)\n")
        engine = NimEngine()
        result = engine.scan_contamination(wdir, {"language": "nim", "dependencies": []})
        unlisted = [w for w in result["warnings"]
                    if "double_lib" in w and "unlisted" in w.lower()]
        assert not unlisted, \
            f"local src import in example must not warn: {result['warnings']}"

    def test_local_src_module_import_from_tests_not_warned(self, tmp_path):
        """A test file importing a local widget src/ module is not an unlisted
        external dep — the scanner must allowlist filenames found in src/.
        Regression for the user-reported false positive on test_rotate_frame.nim
        importing rotate_frame_lib (which lives at src/rotate_frame_lib.nim)."""
        # src module name matches what tests/ will import
        src_code = "func rotate*(x: int): int = x + 1\n"
        test_code = ("import std/unittest\n"
                     "import rotate_frame_lib\n"
                     "suite \"r\":\n  test \"t\":\n    check rotate(1) == 2\n")
        # Use _make_widget directly so we control the src filename
        wdir = _make_widget(tmp_path, "nim", "rotate_frame_lib.nim", src_code,
                            test_code=test_code)
        engine = NimEngine()
        result = engine.scan_contamination(wdir, {"language": "nim", "dependencies": []})
        unlisted = [w for w in result["warnings"]
                    if "rotate_frame_lib" in w and "unlisted" in w.lower()]
        assert not unlisted, \
            f"local src/ module should not warn as unlisted: {result['warnings']}"

    def test_relative_src_import_from_tests_not_warned(self, tmp_path):
        """A test file using a relative-path import (`../src/foo`) into the
        widget's own src/ tree is not an unlisted external dep. The bare
        basename match alone isn't enough — relative-path imports skip
        the bare-name check because root='..' / '.' / 'src'. The scanner
        must take the basename and re-check against localModules.
        Regression for frontend-glfw-input-nim (test imports ../src/glfw_input_lib)."""
        src_code = "func handle*(x: int): int = x + 1\n"
        test_code = ("import std/unittest\n"
                     "import ../src/glfw_input_lib\n"
                     "suite \"g\":\n  test \"t\":\n    check handle(1) == 2\n")
        wdir = _make_widget(tmp_path, "nim", "glfw_input_lib.nim", src_code,
                            test_code=test_code)
        engine = NimEngine()
        result = engine.scan_contamination(wdir, {"language": "nim", "dependencies": []})
        unlisted = [w for w in result["warnings"]
                    if "glfw_input_lib" in w and "unlisted" in w.lower()]
        assert not unlisted, \
            f"relative src/ import from tests must not warn: {result['warnings']}"

    def test_brace_group_stdlib_import_not_warned(self, tmp_path):
        """`import std/[options, terminal]` must be parsed as two stdlib
        imports, not as the bogus modules `[options` and `terminal]`.
        Regression for a false positive on real TUI widgets that use the
        brace-group syntax to import multiple std modules on one line."""
        src_code = ("import std/[options, terminal]\n"
                    "func noop*(x: int): int = x\n")
        result = _scan(tmp_path, "nim", "nim", src_code)
        unlisted = [w for w in result["warnings"] if "unlisted" in w.lower()]
        assert not unlisted, \
            f"brace-group std imports should not warn: {result['warnings']}"

    def test_top_level_var_in_test_file_not_warned(self, tmp_path):
        """Test files often have top-level helper state."""
        clean_src = "proc hello*(): string =\n  \"ok\"\n"
        test_code = ('import std/unittest\n'
                     'var counter = 0\n'
                     'suite "x":\n  test "y":\n    counter.inc; check counter == 1\n')
        result = _scan(tmp_path, "nim", "nim", clean_src, test_code=test_code)
        assert not any("top-level" in w.lower() for w in result["warnings"]), \
            f"top-level vars in test files should not warn: {result['warnings']}"

    def test_nim_stdlib_list_complete(self):
        """Verify the scanner's nimStdlib covers all modules shipped with nim."""
        import subprocess, re as _re

        # Ask the compiler for its own lib directory (works with choosenim,
        # system installs, and any other layout).
        script = 'import std/os; echo getCurrentCompilerExe().parentDir.parentDir / "lib"'
        res = subprocess.run(
            ["nim", "e", "--hints:off", "-"],
            input=script, capture_output=True, text=True, timeout=15,
        )
        if res.returncode != 0:
            pytest.skip(f"nim e failed: {res.stderr.strip()}")
        lib_root = res.stdout.strip()
        if not os.path.isdir(lib_root):
            pytest.skip(f"Could not find nim lib dir at {lib_root}")

        # Collect all .nim module names from stdlib directories
        stdlib_dirs = [
            "pure", "pure/collections", "std", "impure", "pure/concurrency",
        ]
        actual_modules = set()
        for subdir in stdlib_dirs:
            full = os.path.join(lib_root, subdir)
            if not os.path.isdir(full):
                continue
            for fname in os.listdir(full):
                if fname.endswith(".nim"):
                    actual_modules.add(fname[:-4])

        # Extract the scanner's hardcoded list - track bare and slashed forms
        # SEPARATELY so we catch omissions in either form. Nim allows both
        # `import tables` and `import std/tables`; the scanner must know both.
        scanner_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "cartograph",
            "languages", "scanners", "nim_scanner.nim",
        )
        scanner_src = open(scanner_path).read()
        start = scanner_src.index("const nimStdlib = [")
        end = scanner_src.index("].toHashSet()", start)
        block = scanner_src[start:end]
        scanner_bare = set()
        scanner_slashed = set()
        for m in _re.findall(r'"([^"]+)"', block):
            if "/" in m:
                scanner_slashed.add(m.split("/", 1)[1])  # strip "std/"
            else:
                scanner_bare.add(m)

        # Internal/private modules that aren't meant for user import
        internal = {"hashcommon", "tableimpl", "setimpl", "rtarrays"}
        actual_public = actual_modules - internal

        # Modules that legitimately exist in only one form:
        # - `system` is implicit, never imported with std/ prefix
        # - legacy aliases (readline, smtp, sockets, winlean) are bare-only
        bare_only = {"system", "readline", "smtp", "sockets", "winlean"}
        # Currently none — every real stdlib module should have both forms
        slashed_only: set[str] = set()

        # Every actual public module must appear in BOTH forms (with
        # allowlisted exceptions). This catches the bug where `std/tables`
        # masked the absence of bare `tables`.
        missing_bare = (actual_public - scanner_bare) - slashed_only
        missing_slashed = (actual_public - scanner_slashed) - bare_only
        errors = []
        if missing_bare:
            errors.append(f"missing BARE form: {sorted(missing_bare)}")
        if missing_slashed:
            errors.append(f"missing std/ form: {sorted(missing_slashed)}")
        assert not errors, (
            "Nim stdlib modules missing from nim_scanner.nim nimStdlib list:\n"
            + "\n".join(errors)
            + "\nUpdate the hardcoded list in scanners/nim_scanner.nim"
        )

    # -- parametric import grammar coverage --
    # Locks down all 17 legal Nim import forms the scanner must handle.
    # Each case specifies substrings that MUST and MUST NOT appear in warnings.
    # These cases exist because six real bugs were found by exploration;
    # the parametric form makes regressions impossible to reintroduce.
    @pytest.mark.parametrize("label,src,deps,must_have,must_not", [
        # 1. bare stdlib → warn old-style, no unlisted
        ("bare_stdlib",
         "import tables\n", [],
         ["std/"], ["unlisted"]),
        # 2. slashed stdlib → clean
        ("slashed_stdlib",
         "import std/tables\n", [],
         [], ["unlisted", "std/"]),
        # 3. brace group stdlib → clean (regression: was parsed as [options)
        ("brace_group_stdlib",
         "import std/[options, terminal]\n", [],
         [], ["unlisted"]),
        # 4. except clause with comma symbol list → risky only (not unlisted)
        ("except_clause",
         "import std/os except putEnv, getEnv\n", [],
         ["flagged for review"], ["unlisted"]),
        # 5. comma list stdlib → clean
        ("comma_list_stdlib",
         "import std/strutils, std/sequtils\n", [],
         [], ["unlisted"]),
        # 6. multi-line import (dangerous — was silent false negative)
        ("multiline_import",
         "import\n  std/options,\n  std/strutils\n", [],
         [], ["unlisted"]),
        # 7. multi-line brace group
        ("multiline_brace",
         "import std/[\n  options,\n  terminal\n]\n", [],
         [], ["unlisted"]),
        # 8. nested pkg path — root is unlisted
        ("nested_pkg_unlisted",
         "import chronos/apps/http\n", [],
         ["unlisted", "chronos"], []),
        # 9. nested pkg path — root is declared
        ("nested_pkg_declared",
         "import chronos/apps/http\n", ["chronos>=4.0.0"],
         [], ["unlisted"]),
        # 10. rename with `as`
        ("rename_as",
         "import std/os as osys\n", [],
         ["flagged for review"], ["unlisted"]),
        # 11. from form slashed stdlib
        ("from_slashed",
         "from std/strutils import split\n", [],
         [], ["unlisted", "std/"]),
        # 12. from form bare stdlib
        ("from_bare",
         "from strutils import split\n", [],
         ["std/"], ["unlisted"]),
        # 13. truly unlisted third-party
        ("unlisted_pkg",
         "import totallymadeuplib\n", [],
         ["unlisted", "totallymadeuplib"], []),
        # 14. risky stdlib slashed
        ("risky_slashed",
         "import std/httpclient\n", [],
         ["flagged for review"], ["unlisted"]),
        # 15. risky stdlib bare → risky + style warning
        ("risky_bare",
         "import httpclient\n", [],
         ["flagged for review", "prefer std/"], ["unlisted"]),
        # 16. legacy core lib as std/
        ("core_locks",
         "import std/locks\n", [],
         [], ["unlisted"]),
        # 17. bare comma list (two style warnings)
        ("bare_comma_list",
         "import tables, sets\n", [],
         ["std/"], ["unlisted"]),
    ])
    def test_nim_import_forms(self, tmp_path, label, src, deps,
                              must_have, must_not):
        body = src + "proc noop*(): int = 0\n"
        result = _scan(tmp_path, "nim", "nim", body, dependencies=deps)
        # Combine warnings and blocks for substring matching
        all_msgs = " ".join(result["warnings"] + result["blocks"]).lower()
        for needle in must_have:
            assert needle.lower() in all_msgs, (
                f"[{label}] expected '{needle}' in findings, got: "
                f"warnings={result['warnings']} blocks={result['blocks']}"
            )
        for needle in must_not:
            assert needle.lower() not in all_msgs, (
                f"[{label}] unexpected '{needle}' in findings: "
                f"warnings={result['warnings']} blocks={result['blocks']}"
            )


# ---------------------------------------------------------------------------
# 3. SHARED VALIDATION TESTS
# ---------------------------------------------------------------------------

class TestDepPinning:
    """_check_dep_pinning is shared across all engines."""

    @pytest.mark.parametrize("lang,engine_cls", [
        ("python", PythonEngine),
        ("javascript", JavaScriptEngine),
        ("nim", NimEngine),
        ("systemverilog", SystemVerilogEngine),
    ])
    def test_unpinned_dep_fails(self, tmp_path, lang, engine_cls):
        if lang in NEEDS_TOOL:
            _skip_if_missing(lang)
        wdir = _make_widget(tmp_path, lang, f"mod.{EXT[lang]}",
                            CLEAN[lang][0])
        result = engine_cls().validate_widget(wdir, ["somelib"])
        assert result["passed"] is False
        assert "version pin" in result["error"] or "version" in result["error"].lower()

    @pytest.mark.parametrize("lang,engine_cls", [
        ("python", PythonEngine),
        ("javascript", JavaScriptEngine),
        ("nim", NimEngine),
        ("systemverilog", SystemVerilogEngine),
    ])
    def test_pinned_dep_passes_pinning(self, tmp_path, lang, engine_cls):
        if lang in NEEDS_TOOL:
            _skip_if_missing(lang)
        wdir = _make_widget(tmp_path, lang, f"mod.{EXT[lang]}",
                            CLEAN[lang][0])
        result = engine_cls().validate_widget(wdir, ["somelib>=1.0.0"])
        # Should not fail due to pinning (may fail for other reasons)
        if not result["passed"]:
            assert "version pin" not in result.get("error", "")


class TestOrchestrator:
    """The contamination.py module delegates to the right engine."""

    def test_python_delegation(self, tmp_path):
        wdir = _make_widget(tmp_path, "python", "mod.py",
                            'LOG = "/home/user/logs"\n')
        result = scan_contamination(wdir)
        assert any("path" in b.lower() for b in result["blocks"])

    def test_js_delegation(self, tmp_path):
        if not shutil.which("node"):
            pytest.skip("Node.js not installed")
        wdir = _make_widget(tmp_path, "javascript", "mod.js",
                            "const api_key = 'sk-abc123verylongkey'\n")
        result = scan_contamination(wdir)
        assert any("credential" in b.lower() for b in result["blocks"])

    def test_nim_delegation(self, tmp_path):
        if not shutil.which("nim"):
            pytest.skip("Nim not installed")
        wdir = _make_widget(tmp_path, "nim", "mod.nim",
                            'let api_key = "sk-abc123verylongkey"\n')
        result = scan_contamination(wdir)
        assert any("credential" in b.lower() for b in result["blocks"])

    def test_sv_delegation(self, tmp_path):
        if not shutil.which("iverilog"):
            pytest.skip("iverilog not installed")
        wdir = _make_widget(tmp_path, "systemverilog", "mod.sv",
                            "module m;\n    initial begin\n    end\nendmodule\n")
        result = scan_contamination(wdir)
        assert any("initial" in b.lower() for b in result["blocks"])

    def test_missing_manifest_returns_empty(self, tmp_path):
        result = scan_contamination(str(tmp_path))
        assert result["warnings"] == []
        assert len(result["blocks"]) == 1
        # Orchestrator now reports a generic "no manifest found" since it
        # detects kind from filename (widget.json or blueprint.json).
        assert "manifest" in result["blocks"][0].lower()

    def test_unknown_language_uses_base_fallback(self, tmp_path):
        wdir = _make_widget(tmp_path, "lua", "mod.lua",
                            'LOG = "/home/user/logs/app.log"\n')
        result = scan_contamination(wdir)
        assert any("Absolute path" in b for b in result["blocks"])


class TestBaseFallback:
    """The base LanguageEngine provides regex fallback for checks 1-4.

    Check 5 (sleep) is explicitly NOT covered by regex - it requires
    native tooling per the standard in base.py.
    """

    def _scan(self, tmp_path, src_code, ext="txt"):
        wdir = _make_widget(tmp_path, "base", f"module.{ext}", src_code)
        engine = LanguageEngine()
        engine.file_ext = ext
        return engine.scan_contamination(wdir, {"language": "base"})

    def test_abs_path_caught(self, tmp_path):
        result = self._scan(tmp_path, 'x = "/home/user/data"\n')
        assert any("Absolute path" in b for b in result["blocks"])

    def test_credential_caught(self, tmp_path):
        result = self._scan(tmp_path, 'api_key = "sk-abc123verylongkey"\n')
        assert any("credential" in b.lower() for b in result["blocks"])

    def test_url_caught(self, tmp_path):
        result = self._scan(tmp_path,
                            'url = "https://api.company.com/v2/data"\n')
        assert any("URL" in w for w in result["warnings"])

    def test_ip_caught(self, tmp_path):
        result = self._scan(tmp_path, 'host = "192.168.1.50"\n')
        assert any("IP" in b for b in result["blocks"])

    def test_hardcoded_number_warns(self, tmp_path):
        result = self._scan(tmp_path, "TIMEOUT = 30\n")
        assert any("Hardcoded value" in w for w in result["warnings"])

    def test_credential_in_tests_warns(self, tmp_path):
        wdir = _make_widget(tmp_path, "base", "module.txt",
                            "x = 1\n")
        engine = LanguageEngine()
        engine.file_ext = "txt"
        _write(os.path.join(wdir, "tests", "test_module.txt"),
               'password = "fake_test_password_123"\n')
        result = engine.scan_contamination(wdir, {"language": "base"})
        assert not any("credential" in b.lower() for b in result["blocks"])
        assert any("credential" in w.lower() for w in result["warnings"])

    def test_clean_passes(self, tmp_path):
        result = self._scan(tmp_path, "x = compute()\n")
        assert result["blocks"] == []
        assert result["warnings"] == []

    def test_unreadable_source_file_blocks(self, tmp_path):
        wdir = _make_widget(tmp_path, "base", "module.txt", "x = 1\n")
        engine = LanguageEngine()
        engine.file_ext = "txt"
        target = os.path.join(wdir, "src", "module.txt")
        real_open = open

        def fake_open(path, *args, **kwargs):
            if path == target:
                raise OSError("permission denied")
            return real_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=fake_open):
            result = engine.scan_contamination(wdir, {"language": "base"})

        assert any("could not read source file" in b.lower() for b in result["blocks"])

    def test_abs_path_in_comment_not_flagged(self, tmp_path):
        result = self._scan(tmp_path, '// Example: x = "/home/user/.config/app"\n')
        assert not any("Absolute path" in b for b in result["blocks"])

    def test_version_string_not_flagged_as_ip(self, tmp_path):
        # "1.2.3.4" style version strings should not trigger IP detection
        result = self._scan(tmp_path, 'VERSION = "1.2.3.4"\n')
        assert not any("IP" in b for b in result["blocks"])

    def test_real_ip_still_caught(self, tmp_path):
        # An actual IP with multi-digit octets must still be blocked
        result = self._scan(tmp_path, 'host = "192.168.1.50"\n')
        assert any("IP" in b for b in result["blocks"])

    def test_credential_in_comment_still_caught(self, tmp_path):
        # Credentials left in comments (even as examples) must be flagged
        result = self._scan(tmp_path, '// api_key = "sk-abc123verylongkey"\n')
        assert any("credential" in b.lower() for b in result["blocks"])


@pytest.mark.nim
class TestNimRiskyImportIsWarning:
    def test_std_os_is_warning_not_error(self, tmp_path):
        _skip_if_missing("nim")
        result = _scan(tmp_path, "nim", "nim", "import std/os\nproc f*() = discard\n")
        assert not any("risky" in b.lower() for b in result["blocks"])
        assert any("risky" in w.lower() or "i/o" in w.lower() or "network" in w.lower()
                   for w in result["warnings"])

    def test_std_httpclient_is_warning_not_error(self, tmp_path):
        _skip_if_missing("nim")
        result = _scan(tmp_path, "nim", "nim", "import std/httpclient\nproc f*() = discard\n")
        assert not any("risky" in b.lower() for b in result["blocks"])
        assert any("risky" in w.lower() or "i/o" in w.lower() or "network" in w.lower()
                   for w in result["warnings"])


@pytest.mark.python
class TestPythonAbsPathCommentExclusion:
    def test_abs_path_in_comment_not_flagged(self, tmp_path):
        from cartograph.languages.python import PythonEngine
        wdir = _make_widget(tmp_path, "python", "mod.py",
                            '# Example: path = "/home/user/.config"\ndef f(): pass\n')
        engine = PythonEngine()
        result = engine.scan_contamination(wdir, {"dependencies": []})
        assert not any("Absolute path" in b for b in result["blocks"])

    def test_abs_path_in_code_still_caught(self, tmp_path):
        from cartograph.languages.python import PythonEngine
        wdir = _make_widget(tmp_path, "python", "mod.py",
                            'path = "/home/user/.config/app"\n')
        engine = PythonEngine()
        result = engine.scan_contamination(wdir, {"dependencies": []})
        assert any("Absolute path" in b for b in result["blocks"])

    def test_version_string_not_flagged_as_ip(self, tmp_path):
        from cartograph.languages.python import PythonEngine
        wdir = _make_widget(tmp_path, "python", "mod.py",
                            'VERSION = "1.2.3.4"\n')
        engine = PythonEngine()
        result = engine.scan_contamination(wdir, {"dependencies": []})
        assert not any("IP" in b for b in result["blocks"])

    def test_credential_in_comment_still_caught(self, tmp_path):
        from cartograph.languages.python import PythonEngine
        wdir = _make_widget(tmp_path, "python", "mod.py",
                            '# api_key = "sk-abc123verylongkey"\ndef f(): pass\n')
        engine = PythonEngine()
        result = engine.scan_contamination(wdir, {"dependencies": []})
        assert any("credential" in b.lower() for b in result["blocks"])


@pytest.mark.openscad
class TestOpenSCADContamination:
    """OpenSCAD-specific contamination checks."""

    # Valid coordinate frame prepended to test fixtures so the unrelated
    # COORDINATES check doesn't pollute every other rule's assertions.
    # Tests targeting the coordinate-frame rule itself use _scan_raw.
    _COORD_HEADER = (
        "// COORDINATES (cartesian):\n"
        "// Origin: center of part\n"
        "// +X: forward\n"
        "// +Y: right\n"
        "// +Z: up\n"
        "// Right-hand rule.\n"
    )

    def _scan(self, tmp_path, src_content):
        return self._scan_raw(tmp_path, self._COORD_HEADER + src_content)

    def _scan_raw(self, tmp_path, src_content):
        from cartograph.languages.openscad import OpenSCADEngine
        wdir = tmp_path / "widget"
        (wdir / "src").mkdir(parents=True)
        (wdir / "tests").mkdir()
        (wdir / "examples").mkdir()
        (wdir / "src" / "mod.scad").write_text(src_content)
        engine = OpenSCADEngine()
        return engine.scan_contamination(str(wdir), {"dependencies": []})

    # --- top-level geometry ---

    def test_top_level_cube_blocks(self, tmp_path):
        result = self._scan(tmp_path, 'module m(w=10) { cube([w,w,w]); }\ncube([5,5,5]);\n')
        assert any("top-level statement" in b.lower() for b in result["blocks"])

    def test_top_level_sphere_blocks(self, tmp_path):
        result = self._scan(tmp_path, 'module m(r=5) { sphere(r); }\nsphere(r=3);\n')
        assert any("top-level statement" in b.lower() for b in result["blocks"])

    def test_geometry_inside_module_is_clean(self, tmp_path):
        result = self._scan(tmp_path, 'module m(w=10) { cube([w,w,w]); }\n')
        assert not any("top-level statement" in b.lower() for b in result["blocks"])

    # --- parameters without defaults ---

    def test_param_without_default_blocks(self, tmp_path):
        result = self._scan(tmp_path, 'module m(width, height=10) { cube([width,height,5]); }\n')
        assert any("no default" in b.lower() for b in result["blocks"])
        assert any("width" in b for b in result["blocks"])

    def test_all_params_have_defaults_is_clean(self, tmp_path):
        result = self._scan(tmp_path, 'module m(width=20, height=10) { cube([width,height,5]); }\n')
        assert not any("no default" in b.lower() for b in result["blocks"])

    def test_no_params_is_clean(self, tmp_path):
        result = self._scan(tmp_path, 'module m() { cube([10,10,10]); }\n')
        assert not any("no default" in b.lower() for b in result["blocks"])

    # --- parameter unit comments ---

    def test_param_without_unit_comment_warns(self, tmp_path):
        result = self._scan(tmp_path, 'module m(width=20, height=10) { cube([width,height,5]); }\n')
        assert any("unit comment" in w.lower() for w in result["warnings"])

    def test_multiline_params_with_comments_clean(self, tmp_path):
        src = 'module m(\n    width  = 20,  // mm\n    height = 10   // mm\n) { cube([width,height,5]); }\n'
        result = self._scan(tmp_path, src)
        assert not any("unit comment" in w.lower() for w in result["warnings"])

    # --- existing checks still work ---

    def test_absolute_path_blocks(self, tmp_path):
        result = self._scan(tmp_path, 'module m(w=10) { cube([w,w,w]); }\nuse </abs/path/lib.scad>\n')
        assert any("absolute path" in b.lower() for b in result["blocks"])

    def test_credential_blocks(self, tmp_path):
        result = self._scan(tmp_path, 'module m(w=10) { cube([w,w,w]); }\napi_key = "sk-secret123abc";\n')
        assert any("credential" in b.lower() for b in result["blocks"])

    # --- coordinate frame ---

    def test_no_coord_block_blocks(self, tmp_path):
        result = self._scan_raw(tmp_path, 'module m(w=10) { cube([w,w,w]); }\n')
        assert any("coordinate frame" in b.lower() for b in result["blocks"])

    def test_coord_block_with_todo_blocks(self, tmp_path):
        content = (
            "// COORDINATES (cartesian):\n"
            "// Origin: [TODO: e.g. center of part]\n"
            "// +X: [TODO]\n"
            "// +Y: right\n"
            "// +Z: up\n"
            "module m(w=10) { cube([w,w,w]); }\n"
        )
        result = self._scan_raw(tmp_path, content)
        assert any("[TODO]" in b for b in result["blocks"])

    def test_filled_cartesian_passes(self, tmp_path):
        content = self._COORD_HEADER + "module m(w=10) { cube([w,w,w]); }\n"
        result = self._scan_raw(tmp_path, content)
        assert not any("coordinate" in b.lower() for b in result["blocks"])

    def test_filled_cylindrical_passes(self, tmp_path):
        content = (
            "// COORDINATES (cylindrical):\n"
            "// Axis: +Z\n"
            "// Origin: base on axis\n"
            "// theta=0: +X\n"
            "// +theta: counterclockwise looking down +Z\n"
            "// Right-hand rule.\n"
            "module gear(teeth=20) { cylinder(h=5, r=teeth); }\n"
        )
        result = self._scan_raw(tmp_path, content)
        assert not any("coordinate" in b.lower() for b in result["blocks"])

    def test_filled_spherical_passes(self, tmp_path):
        content = (
            "// COORDINATES (spherical):\n"
            "// Origin: geometric center\n"
            "// theta (azimuth) zero: +X\n"
            "// phi (polar) zero: +Z\n"
            "// Right-hand rule.\n"
            "module ball(r=5) { sphere(r); }\n"
        )
        result = self._scan_raw(tmp_path, content)
        assert not any("coordinate" in b.lower() for b in result["blocks"])

    def test_dual_systems_one_filled_passes(self, tmp_path):
        """A filled cartesian + a [TODO]-laden cylindrical should still pass —
        author left both blocks but only one is real, that's fine."""
        content = (
            self._COORD_HEADER
            + "// COORDINATES (cylindrical):\n"
            + "// Axis: [TODO]\n"
            + "module m(w=10) { cube([w,w,w]); }\n"
        )
        result = self._scan_raw(tmp_path, content)
        # First valid block satisfies the rule
        assert not any("coordinate" in b.lower() for b in result["blocks"])

    def test_cartesian_missing_z_axis_blocks(self, tmp_path):
        content = (
            "// COORDINATES (cartesian):\n"
            "// Origin: center of part\n"
            "// +X: forward\n"
            "// +Y: right\n"
            "// (no +Z declared)\n"
            "module m(w=10) { cube([w,w,w]); }\n"
        )
        result = self._scan_raw(tmp_path, content)
        assert any("+z:" in b.lower() and "missing" in b.lower()
                   for b in result["blocks"])

    def test_cylindrical_missing_axis_blocks(self, tmp_path):
        content = (
            "// COORDINATES (cylindrical):\n"
            "// Origin: base of part\n"
            "// theta=0: +X\n"
            "module m(w=10) { cube([w,w,w]); }\n"
        )
        result = self._scan_raw(tmp_path, content)
        assert any("axis:" in b.lower() and "missing" in b.lower()
                   for b in result["blocks"])

    def test_spherical_missing_phi_blocks(self, tmp_path):
        content = (
            "// COORDINATES (spherical):\n"
            "// Origin: center\n"
            "// theta (azimuth) zero: +X\n"
            "module m(w=10) { cube([w,w,w]); }\n"
        )
        result = self._scan_raw(tmp_path, content)
        assert any("phi" in b.lower() and "missing" in b.lower()
                   for b in result["blocks"])

    def test_unknown_system_label_does_not_count(self, tmp_path):
        content = (
            "// COORDINATES (toroidal):\n"
            "// Axis: whatever\n"
            "module m(w=10) { cube([w,w,w]); }\n"
        )
        result = self._scan_raw(tmp_path, content)
        # Only cartesian/cylindrical/spherical are recognized, so this file
        # has no valid block and should be flagged as missing.
        assert any("coordinate frame" in b.lower() for b in result["blocks"])


@pytest.mark.systemverilog
class TestSystemVerilogContamination:
    """SystemVerilog-specific contamination checks."""

    @pytest.fixture(autouse=True)
    def _require_iverilog(self):
        if not shutil.which("iverilog"):
            pytest.skip("iverilog not installed")

    def _scan(self, tmp_path, src_content, dependencies=None):
        wdir = tmp_path / "widget"
        (wdir / "src").mkdir(parents=True)
        (wdir / "tests").mkdir()
        (wdir / "examples").mkdir()
        (wdir / "src" / "mod.sv").write_text(src_content)
        engine = SystemVerilogEngine()
        return engine.scan_contamination(
            str(wdir), {"language": "systemverilog",
                        "dependencies": dependencies or []})

    # --- clean code ---

    def test_clean_module_no_findings(self, tmp_path):
        result = self._scan(tmp_path,
            "module m #(parameter int W = 8)(\n"
            "    input logic clk, input logic rst_n,\n"
            "    input logic [W-1:0] d, output logic [W-1:0] q\n"
            ");\n    always_ff @(posedge clk) begin\n"
            "        if (!rst_n) q <= '0; else q <= d;\n"
            "    end\nendmodule\n")
        assert result["blocks"] == [], f"unexpected blocks: {result['blocks']}"

    # --- vendor primitives ---

    def test_vendor_primitive_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "module m; BUFG clk_buf (.I(clk), .O(buf_clk)); endmodule\n")
        assert any("vendor" in b.lower() for b in result["blocks"])

    def test_vendor_primitive_allowed_with_dep(self, tmp_path):
        result = self._scan(tmp_path,
            "module m; BUFG clk_buf (.I(clk), .O(buf_clk)); endmodule\n",
            dependencies=["xilinx-unisim"])
        assert not any("vendor" in b.lower() for b in result["blocks"])

    def test_altera_primitive_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "module m; ALTPLL #(.WIDTH(8)) pll (.inclk0(clk)); endmodule\n")
        assert any("vendor" in b.lower() for b in result["blocks"])

    def test_altera_allowed_with_dep(self, tmp_path):
        result = self._scan(tmp_path,
            "module m; ALTPLL #(.WIDTH(8)) pll (.inclk0(clk)); endmodule\n",
            dependencies=["altera-ip"])
        assert not any("vendor" in b.lower() for b in result["blocks"])

    # --- initial blocks ---

    def test_initial_block_in_src_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "module m;\n    initial begin\n        d_out = '0;\n    end\nendmodule\n")
        assert any("initial" in b.lower() for b in result["blocks"])

    # --- #delay ---

    def test_delay_in_src_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "module m;\n    always_ff @(posedge clk) begin\n"
            "        #10 q <= d;\n    end\nendmodule\n")
        assert any("delay" in b.lower() for b in result["blocks"])

    # --- timescale ---

    def test_timescale_in_src_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "`timescale 1ns/1ps\nmodule m; endmodule\n")
        assert any("timescale" in b.lower() for b in result["blocks"])

    # --- legacy always ---

    def test_legacy_always_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "module m;\n    always @(posedge clk) begin\n"
            "        q <= d;\n    end\nendmodule\n")
        assert any("always @" in b or "Verilog-2001" in b for b in result["blocks"])

    # --- $display family ---

    def test_display_in_src_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "module m;\n    always_ff @(posedge clk) begin\n"
            "        $display(\"debug\");\n        q <= d;\n"
            "    end\nendmodule\n")
        assert any("display" in b.lower() for b in result["blocks"])

    def test_monitor_in_src_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "module m;\n    always_comb begin\n"
            "        $monitor(\"watch\");\n    end\nendmodule\n")
        assert any("monitor" in b.lower() for b in result["blocks"])

    # --- $readmemh hardcoded path ---

    def test_readmemh_hardcoded_path_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "module m;\n    logic [7:0] mem [0:255];\n"
            "    always_comb $readmemh(\"/data/rom.hex\", mem);\n"
            "endmodule\n")
        assert any("readmemh" in b.lower() for b in result["blocks"])

    # --- blocking/non-blocking assignment ---

    def test_blocking_in_always_ff_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "module m(input logic clk, input logic d, output logic q);\n"
            "    always_ff @(posedge clk) begin\n"
            "        q = d;\n"
            "    end\nendmodule\n")
        assert any("blocking" in b.lower() and "always_ff" in b
                    for b in result["blocks"])

    def test_nonblocking_in_always_comb_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "module m(input logic a, output logic b);\n"
            "    always_comb begin\n"
            "        b <= a;\n"
            "    end\nendmodule\n")
        assert any("non-blocking" in b.lower() and "always_comb" in b
                    for b in result["blocks"])

    def test_for_loop_in_always_ff_not_false_positive(self, tmp_path):
        result = self._scan(tmp_path,
            "module m #(parameter int D = 4)(\n"
            "    input logic clk, input logic rst_n,\n"
            "    output logic [7:0] s [0:D-1]\n"
            ");\n    always_ff @(posedge clk) begin\n"
            "        if (!rst_n)\n"
            "            for (int i = 0; i < D; i++) s[i] <= '0;\n"
            "    end\nendmodule\n")
        assert not any("blocking" in b.lower() for b in result["blocks"])

    def test_typedef_enum_not_false_positive(self, tmp_path):
        result = self._scan(tmp_path,
            "module m;\n"
            "    typedef enum logic [1:0] {\n"
            "        IDLE = 2'b00, RUN = 2'b01\n"
            "    } state_t;\n"
            "    state_t st;\nendmodule\n")
        assert not any("hardcoded" in w.lower() for w in result["warnings"])

    def test_typedef_enum_with_nested_concat_initializer(self, tmp_path):
        # Regression: the prior regex-blanking approach used [^}]* and stopped
        # at the first } inside the enum body, so a SV concatenation literal
        # like {2'b01, 2'b10} broke enum exclusion and exposed later constants
        # to the hardcoded-numeric scanner. block_walker handles nested {}
        # correctly via depth counting.
        result = self._scan(tmp_path,
            "module m;\n"
            "    typedef enum logic [3:0] {\n"
            "        X = {2'b01, 2'b10},\n"
            "        Y = 4'hAB\n"
            "    } t;\n"
            "    t st;\nendmodule\n")
        # 4'hAB is inside the enum body and must not be flagged
        for w in result["warnings"]:
            assert "4'hAB" not in w

    # --- comments don't false-positive ---

    def test_vendor_in_comment_not_flagged(self, tmp_path):
        result = self._scan(tmp_path,
            "// This replaces a LUT6 with generic logic\n"
            "/* Previously used BUFG */\n"
            "module m; endmodule\n")
        assert not any("vendor" in b.lower() for b in result["blocks"])


@pytest.mark.php
class TestPhpSpecific:
    """PHP-only validation and contamination checks."""

    @pytest.fixture(autouse=True)
    def _require_php(self):
        if not shutil.which("php"):
            pytest.skip("PHP not installed")

    def _scan(self, tmp_path, src_content, test_content="", deps=None):
        wdir = tmp_path / "widget"
        (wdir / "src").mkdir(parents=True)
        (wdir / "tests").mkdir()
        (wdir / "examples").mkdir()
        (wdir / "src" / "mod.php").write_text(src_content)
        if test_content:
            (wdir / "tests" / "test_mod.php").write_text(test_content)
        engine = PhpEngine()
        return engine.scan_contamination(
            str(wdir), {"language": "php", "dependencies": deps or []}
        )

    # -- validate_widget --

    def test_echo_in_src_fails(self, tmp_path):
        wdir = _make_widget(tmp_path, "php", "mod.php",
                            "<?php\necho 'debug';\n")
        result = PhpEngine().validate_widget(wdir, [])
        assert result["passed"] is True  # validate_widget does syntax+pinning only

    def test_echo_in_src_blocked_by_scanner(self, tmp_path):
        result = self._scan(tmp_path, "<?php\necho 'debug';\n")
        assert any("echo" in b.lower() for b in result["blocks"])

    def test_clean_passes(self, tmp_path):
        wdir = _make_widget(tmp_path, "php", "mod.php",
                            "<?php\nclass Item { public function get(): mixed { return 1; } }\n")
        result = PhpEngine().validate_widget(wdir, [])
        assert result["passed"] is True

    def test_syntax_error_fails(self, tmp_path):
        wdir = _make_widget(tmp_path, "php", "mod.php",
                            "<?php\nclass Item { public function get() { return }\n")
        result = PhpEngine().validate_widget(wdir, [])
        assert result["passed"] is False
        assert "syntax" in result["error"].lower()

    # -- WordPress globals --

    def test_wp_function_in_src_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "<?php\nfunction my_func() { wp_enqueue_script('my-js'); }\n")
        assert any("wordpress" in b.lower() for b in result["blocks"])

    def test_add_action_in_src_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "<?php\nadd_action('init', 'my_init');\n")
        assert any("wordpress" in b.lower() for b in result["blocks"])

    def test_wpdb_in_src_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "<?php\nglobal $wpdb;\n$wpdb->query('SELECT 1');\n")
        assert any("wordpress" in b.lower() for b in result["blocks"])

    def test_get_option_in_src_blocks(self, tmp_path):
        result = self._scan(tmp_path,
            "<?php\n$val = get_option('my_setting');\n")
        assert any("wordpress" in b.lower() for b in result["blocks"])

    def test_wp_in_comment_not_blocked(self, tmp_path):
        result = self._scan(tmp_path,
            "<?php\n// This widget has no wp_enqueue_script dependency\n"
            "class Item { public function get(): mixed { return 1; } }\n")
        assert not any("wordpress" in b.lower() for b in result["blocks"])

    # -- echo isolation --

    def test_echo_in_example_not_blocked(self, tmp_path):
        """Examples are allowed to use echo - it's the PHP print equivalent."""
        wdir = tmp_path / "widget2"
        (wdir / "src").mkdir(parents=True)
        (wdir / "tests").mkdir()
        (wdir / "examples").mkdir()
        (wdir / "src" / "mod.php").write_text(
            "<?php\nclass Item { public function get(): mixed { return 1; } }\n")
        (wdir / "examples" / "example_usage.php").write_text(
            "<?php\n$x = 1;\necho $x . PHP_EOL;\n")
        engine = PhpEngine()
        result = engine.scan_contamination(
            str(wdir), {"language": "php", "dependencies": []})
        assert not any("echo" in b.lower() for b in result["blocks"])

    # -- unlisted use statement --

    def test_psr_namespace_not_warned(self, tmp_path):
        """PSR is a standards body, not a real package - should be excluded."""
        result = self._scan(tmp_path,
            "<?php\nuse Psr\\Log\\LoggerInterface;\n"
            "class Item { public function get(): mixed { return 1; } }\n")
        assert not any("unlisted" in w.lower() for w in result["warnings"])

    def test_cartograph_namespace_not_warned(self, tmp_path):
        """Internal Cartograph namespace imports must not warn."""
        result = self._scan(tmp_path,
            "<?php\nuse Cartograph\\MyModule\\MyClass;\n"
            "class Item { public function get(): mixed { return 1; } }\n")
        assert not any("unlisted" in w.lower() for w in result["warnings"])

    def test_hardcoded_constant_warns(self, tmp_path):
        result = self._scan(tmp_path,
            "<?php\nclass Item { private $TIMEOUT = 30; }\n")
        assert any("hardcoded" in w.lower() for w in result["warnings"])

    # -- resolver-based unlisted namespace check --

    def _scan_with_autoload(self, tmp_path, src_content, psr4_roots,
                             test_content="", example_content=""):
        """Build a widget with a pre-populated vendor/composer/autoload_psr4.php
        containing the given namespace roots, so scan_contamination uses the
        real-resolver path instead of the heuristic fallback."""
        wdir = tmp_path / "widget"
        (wdir / "src").mkdir(parents=True)
        (wdir / "tests").mkdir()
        (wdir / "examples").mkdir()
        (wdir / "vendor" / "composer").mkdir(parents=True)
        (wdir / "composer.json").write_text('{"name":"test/widget"}')
        psr4_entries = "\n".join(
            f"    '{root}\\\\' => array(\\$vendorDir . '/fake')," for root in psr4_roots
        )
        (wdir / "vendor" / "composer" / "autoload_psr4.php").write_text(
            "<?php\n$vendorDir = dirname(__DIR__);\nreturn array(\n"
            + psr4_entries + "\n);\n"
        )
        (wdir / "src" / "mod.php").write_text(src_content)
        if test_content:
            (wdir / "tests" / "test_mod.php").write_text(test_content)
        if example_content:
            (wdir / "examples" / "example_usage.php").write_text(example_content)
        engine = PhpEngine()
        return engine.scan_contamination(
            str(wdir), {"language": "php", "dependencies": []}
        )

    def test_resolver_blocks_unknown_namespace_in_src(self, tmp_path):
        """When composer autoload exists and namespace is not there, src/
        use statements must BLOCK (authoritative resolution)."""
        result = self._scan_with_autoload(tmp_path,
            "<?php\nuse Nesbot\\Carbon\\Carbon;\n"
            "class Item { public function get(): mixed { return 1; } }\n",
            psr4_roots=["Symfony"])  # Carbon is NOT in autoload
        assert any("nesbot" in b.lower() and "unlisted" in b.lower()
                   for b in result["blocks"]), \
            f"unresolvable namespace must block in src/: {result}"

    def test_resolver_accepts_known_namespace(self, tmp_path):
        """When composer autoload lists the namespace root, no warning."""
        result = self._scan_with_autoload(tmp_path,
            "<?php\nuse Carbon\\Carbon;\n"
            "class Item { public function get(): mixed { return 1; } }\n",
            psr4_roots=["Carbon"])
        assert not any("unlisted" in b.lower() for b in result["blocks"])
        assert not any("unlisted" in w.lower() for w in result["warnings"])

    def test_resolver_handles_vendor_namespace_mismatch(self, tmp_path):
        """Regression for Nesbot\\Carbon case: composer package is nesbot/carbon
        but namespace root is Carbon. The autoload table is ground truth and
        reports Carbon, so a use of Carbon\\... must not flag even though the
        vendor prefix differs."""
        result = self._scan_with_autoload(tmp_path,
            "<?php\nuse Carbon\\CarbonImmutable;\n"
            "class Item { public function get(): mixed { return 1; } }\n",
            psr4_roots=["Carbon"])
        assert not any("unlisted" in b.lower() for b in result["blocks"])
        assert not any("unlisted" in w.lower() for w in result["warnings"])

    def test_resolver_warns_in_tests_not_blocks(self, tmp_path):
        """Unknown namespace in tests/ warns but does not block."""
        result = self._scan_with_autoload(tmp_path,
            "<?php\nclass Item { public function get(): mixed { return 1; } }\n",
            psr4_roots=["Carbon"],
            test_content="<?php\nuse Faker\\Factory;\n"
                         "class ItemTest extends \\PHPUnit\\Framework\\TestCase {}\n")
        assert any("faker" in w.lower() and "unlisted" in w.lower()
                   for w in result["warnings"]), \
            f"unknown namespace in test must warn: {result}"
        assert not any("faker" in b.lower() for b in result["blocks"]), \
            f"unknown namespace in test must not block: {result}"


@pytest.mark.terraform
class TestTerraformSpecific:
    """Terraform-only contamination checks - module/consumer split rules."""

    def _make_tf_widget(self, tmp_path, src_code, test_code="", example_code=""):
        return _make_widget(tmp_path, "terraform", "module.tf", src_code,
                            test_code=test_code, example_code=example_code)

    def test_provider_block_in_src_blocks(self, tmp_path):
        """Modules must not declare providers - that's the consumer's choice."""
        result = _scan(tmp_path, "terraform", "tf",
                       'provider "aws" {\n  region = "us-east-1"\n}\n')
        assert any("provider" in b.lower() and "block" in b.lower()
                   for b in result["blocks"]), \
            f"provider block in src/ must block: {result}"

    def test_provider_block_in_tests_allowed(self, tmp_path):
        """tests/ is a root config - it MUST declare a provider so validate runs."""
        result = _scan(tmp_path, "terraform", "tf",
                       'resource "null_resource" "x" {}\n',
                       test_code='provider "aws" { region = "us-east-1" }\n')
        assert not any("provider" in b.lower() and "block" in b.lower()
                       for b in result["blocks"]), \
            f"provider block in tests/ must not block: {result['blocks']}"

    def test_backend_block_in_src_blocks(self, tmp_path):
        """Backend choice belongs to the consumer's root config, never the module."""
        result = _scan(tmp_path, "terraform", "tf",
                       'terraform {\n  backend "s3" {\n    bucket = "x"\n  }\n}\n')
        assert any("backend" in b.lower() for b in result["blocks"]), \
            f"backend block in src/ must block: {result}"

    def test_real_aws_account_id_blocks(self, tmp_path):
        """Real 12-digit AWS account IDs in ARN strings are blocked."""
        result = _scan(tmp_path, "terraform", "tf",
                       'locals {\n  arn = "arn:aws:iam::987654321098:role/admin"\n}\n')
        assert any("account" in b.lower() and "987654321098" in b
                   for b in result["blocks"]), \
            f"real AWS account ID must block: {result}"

    def test_placeholder_aws_account_id_allowed(self, tmp_path):
        """000000000000 and 123456789012 are documented placeholders."""
        result = _scan(tmp_path, "terraform", "tf",
                       'locals {\n  arn = "arn:aws:iam::000000000000:role/x"\n}\n')
        assert not any("account" in b.lower() for b in result["blocks"]), \
            f"placeholder account ID must not block: {result['blocks']}"

    def test_credential_in_comment_not_blocked(self, tmp_path):
        """Comment-stripping pre-pass must keep credential-in-comment off the
        block list - false positives in docs would make the scanner unusable."""
        result = _scan(tmp_path, "terraform", "tf",
                       '# example: password = "hunter2-do-not-use"\n'
                       'resource "null_resource" "x" {}\n')
        assert not any("credential" in b.lower() for b in result["blocks"]), \
            f"credential in comment must not block: {result['blocks']}"

    def test_provider_block_in_comment_not_blocked(self, tmp_path):
        """Block check must respect comment stripping."""
        result = _scan(tmp_path, "terraform", "tf",
                       '# Modules cannot have a provider "aws" {} block.\n'
                       'resource "null_resource" "x" {}\n')
        assert not any("provider" in b.lower() and "block" in b.lower()
                       for b in result["blocks"]), \
            f"provider block in comment must not block: {result['blocks']}"

    def test_url_in_tests_warns(self, tmp_path):
        """URL warnings apply everywhere, including tests."""
        result = _scan(tmp_path, "terraform", "tf",
                       'resource "null_resource" "x" {}\n',
                       test_code='locals { api = "https://api.realsite.com/v1" }\n')
        assert any("url" in w.lower() for w in result["warnings"]), \
            f"hardcoded URL in tests must warn: {result['warnings']}"

    def test_ip_in_tests_warns_not_blocks(self, tmp_path):
        """IPs block in src/, warn in tests/examples (matches other languages)."""
        result = _scan(tmp_path, "terraform", "tf",
                       'resource "null_resource" "x" {}\n',
                       test_code='locals { host = "203.0.113.5" }\n')
        assert not any("ip" in b.lower() for b in result["blocks"]), \
            f"IP in tests/ must not block: {result['blocks']}"
        assert any("ip" in w.lower() for w in result["warnings"]), \
            f"IP in tests/ must warn: {result['warnings']}"


# ---------------------------------------------------------------------------
# Go-specific checks (go/ast native scanner)
# ---------------------------------------------------------------------------

class TestGoSpecific:
    """Checks unique to the Go engine's native scanner."""

    def _go_scan(self, tmp_path, src_code, **kw):
        _skip_if_missing("go")
        return _scan(tmp_path, "go", "go", src_code, **kw)

    def test_fmt_println_in_src_blocks(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\nimport "fmt"\n\n'
            'func Hello() { fmt.Println("debug") }\n')
        assert any("console output" in b for b in result["blocks"]), \
            f"fmt.Println in src must block: {result}"

    def test_fmt_println_in_string_not_blocked(self, tmp_path):
        """AST-based scanning: calls named inside string literals don't trip."""
        result = self._go_scan(tmp_path,
            'package module\n\n'
            'func Doc() string { return "call fmt.Println(x) to print" }\n')
        assert not any("console output" in b for b in result["blocks"]), \
            f"fmt.Println in a string must not block: {result['blocks']}"

    def test_fmt_println_in_comment_not_blocked(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\n'
            '// Use fmt.Println(x) in your own code to inspect values.\n'
            'func Hello() string { return "world" }\n')
        assert not any("console output" in b for b in result["blocks"]), \
            f"fmt.Println in a comment must not block: {result['blocks']}"

    def test_os_exit_in_src_blocks(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\nimport "os"\n\n'
            'func Quit() { os.Exit(1) }\n')
        assert any("exit" in b.lower() for b in result["blocks"]), \
            f"os.Exit in src must block: {result}"

    def test_log_fatal_in_src_blocks(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\nimport "log"\n\n'
            'func Quit() { log.Fatal("boom") }\n')
        assert any("exit" in b.lower() for b in result["blocks"]), \
            f"log.Fatal in src must block: {result}"

    def test_panic_in_init_blocks(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\n'
            'func init() { panic("boom") }\n')
        assert any("init" in b.lower() for b in result["blocks"]), \
            f"panic in init must block: {result}"

    def test_panic_in_regular_func_allowed(self, tmp_path):
        """panic in a normal function is a legitimate Go idiom (Must* helpers)."""
        result = self._go_scan(tmp_path,
            'package module\n\n'
            'func MustPositive(n int) int {\n'
            '\tif n <= 0 {\n\t\tpanic("not positive")\n\t}\n'
            '\treturn n\n}\n')
        assert result["blocks"] == [], \
            f"panic outside init must not block: {result['blocks']}"

    def test_top_level_var_warns(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\nvar counter int\n\n'
            'func Inc() { counter++ }\n')
        assert any("mutable state" in w.lower() for w in result["warnings"]), \
            f"top-level var must warn: {result['warnings']}"

    def test_top_level_const_no_mutable_warning(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\nconst Greeting = "hello"\n\n'
            'func Hello() string { return Greeting }\n')
        assert not any("mutable state" in w.lower() for w in result["warnings"]), \
            f"const must not warn as mutable state: {result['warnings']}"

    def test_fmt_sprintf_allowed(self, tmp_path):
        """Only printing variants block - fmt.Sprintf/Errorf are pure."""
        result = self._go_scan(tmp_path,
            'package module\n\nimport "fmt"\n\n'
            'func Greet(name string) string { return fmt.Sprintf("hi %s", name) }\n')
        assert result["blocks"] == [], \
            f"fmt.Sprintf must not block: {result['blocks']}"

    def test_println_allowed_in_example(self, tmp_path):
        """Examples are package main demos - console output is their job."""
        result = self._go_scan(tmp_path,
            'package module\n\nfunc Hello() string { return "world" }\n',
            example_code='package main\n\nimport "fmt"\n\n'
                         'func main() { fmt.Println("demo") }\n')
        assert result["blocks"] == [], \
            f"fmt.Println in examples must not block: {result['blocks']}"

    def test_sentinel_error_var_allowed(self, tmp_path):
        """var ErrX = errors.New(...) is the idiomatic Go error contract."""
        result = self._go_scan(tmp_path,
            'package module\n\nimport "errors"\n\n'
            'var ErrNotFound = errors.New("not found")\n\n'
            'func Find() error { return ErrNotFound }\n')
        assert not any("mutable state" in w.lower() for w in result["warnings"]), \
            f"sentinel error var must not warn: {result['warnings']}"

    def test_version_string_not_flagged_as_ip(self, tmp_path):
        """Dotted runs longer than 4 octets (1.2.3.4.5) are versions, not IPs."""
        result = self._go_scan(tmp_path,
            'package module\n\n'
            'func V() string { return "1.2.3.4.5" }\n')
        assert not any("ip" in b.lower() for b in result["blocks"]), \
            f"version string must not block as IP: {result['blocks']}"

    # -- Modern-standards checks --

    def test_ioutil_import_blocks_in_src(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\nimport "io/ioutil"\n\n'
            '// Read wraps ioutil.\nfunc Read(p string) ([]byte, error) '
            '{ return ioutil.ReadFile(p) }\n')
        assert any("io/ioutil" in b or "deprecated" in b.lower()
                   for b in result["blocks"]), \
            f"io/ioutil must block in src: {result}"

    def test_math_rand_warns(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\nimport "math/rand"\n\n'
            '// Roll returns a die roll.\nfunc Roll() int { return rand.Intn(6) }\n')
        assert any("rand" in w.lower() for w in result["warnings"]), \
            f"math/rand must warn: {result['warnings']}"

    def test_math_rand_v2_not_flagged(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\nimport "math/rand/v2"\n\n'
            '// Roll returns a die roll.\nfunc Roll() int { return rand.IntN(6) }\n')
        assert not any("rand" in w.lower() for w in result["warnings"]), \
            f"math/rand/v2 must not warn: {result['warnings']}"

    def test_empty_interface_warns(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\n'
            '// Box holds anything.\ntype Box struct{ V interface{} }\n')
        assert any("interface{}" in w or "any" in w.lower()
                   for w in result["warnings"]), \
            f"interface{{}} must warn: {result['warnings']}"

    def test_any_alias_not_flagged(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\n'
            '// Box holds anything.\ntype Box struct{ V any }\n')
        assert not any("interface" in w.lower() for w in result["warnings"]), \
            f"any must not warn: {result['warnings']}"

    def test_missing_doc_warns(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\nfunc Exported() string { return "x" }\n')
        assert any("doc" in w.lower() for w in result["warnings"]), \
            f"undocumented exported func must warn: {result['warnings']}"

    def test_documented_export_no_warning(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\n// Exported does a thing.\n'
            'func Exported() string { return "x" }\n')
        assert not any("doc" in w.lower() for w in result["warnings"]), \
            f"documented export must not warn: {result['warnings']}"

    def test_unexported_no_doc_warning(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\nfunc internal() string { return "x" }\n')
        assert not any("doc" in w.lower() for w in result["warnings"]), \
            f"unexported func must not require doc: {result['warnings']}"

    def test_bare_goroutine_warns(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\n// Spawn starts work.\n'
            'func Spawn() { go func() { _ = 1 }() }\n')
        assert any("goroutine" in w.lower() for w in result["warnings"]), \
            f"anonymous goroutine must warn: {result['warnings']}"

    def test_error_equality_warns(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\nimport "errors"\n\n'
            '// ErrGone is a sentinel.\nvar ErrGone = errors.New("gone")\n\n'
            '// Check compares an error.\n'
            'func Check(err error) bool { return err == ErrGone }\n')
        assert any("errors.is" in w.lower() for w in result["warnings"]), \
            f"err == sentinel must warn: {result['warnings']}"

    def test_err_nil_comparison_not_flagged(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\n// Ok reports success.\n'
            'func Ok(err error) bool { return err == nil }\n')
        assert not any("errors.is" in w.lower() for w in result["warnings"]), \
            f"err == nil must not warn: {result['warnings']}"

    def test_errors_is_not_flagged(self, tmp_path):
        result = self._go_scan(tmp_path,
            'package module\n\nimport "errors"\n\n'
            '// ErrGone is a sentinel.\nvar ErrGone = errors.New("gone")\n\n'
            '// Check uses errors.Is.\n'
            'func Check(err error) bool { return errors.Is(err, ErrGone) }\n')
        assert not any("errors.is" in w.lower() for w in result["warnings"]), \
            f"errors.Is is the correct form, must not warn: {result['warnings']}"


_SUBCKT_CLEAN = (
    "* clean parametric block\n"
    ".subckt lowpass_rc in out params: r=1k c=159n\n"
    "R1 in out {r}\n"
    "C1 out 0 {c}\n"
    ".ends lowpass_rc\n"
)


class TestRustSpecific:
    """Checks unique to the Rust engine's native scanner, including the
    comment/string/raw-string awareness that regex can't do reliably."""

    def _rust_scan(self, tmp_path, src_code, **kw):
        _skip_if_missing("rust")
        return _scan(tmp_path, "rust", "rs", src_code, **kw)

    def test_println_in_src_blocks(self, tmp_path):
        result = self._rust_scan(tmp_path,
            'pub fn hello() { println!("debug"); }\n')
        assert any("console output" in b for b in result["blocks"]), \
            f"println! in src must block: {result}"

    def test_println_in_string_not_blocked(self, tmp_path):
        result = self._rust_scan(tmp_path,
            'pub fn doc() -> &\'static str { "call println!(x) to print" }\n')
        assert not any("console output" in b for b in result["blocks"]), \
            f"println! in a string must not block: {result['blocks']}"

    def test_println_in_raw_string_not_blocked(self, tmp_path):
        result = self._rust_scan(tmp_path,
            'pub fn doc() -> &\'static str { r#"println!("x") and unsafe {}"# }\n')
        assert result["blocks"] == [], \
            f"contents of a raw string must not block: {result['blocks']}"

    def test_println_in_comment_not_blocked(self, tmp_path):
        result = self._rust_scan(tmp_path,
            '/// Call println!(x) in your own code to inspect values.\n'
            'pub fn hello() -> String { String::from("world") }\n')
        assert not any("console output" in b for b in result["blocks"]), \
            f"println! in a doc comment must not block: {result['blocks']}"

    def test_process_exit_in_src_blocks(self, tmp_path):
        result = self._rust_scan(tmp_path,
            'pub fn quit() { std::process::exit(1); }\n')
        assert any("exit" in b.lower() for b in result["blocks"]), \
            f"process::exit in src must block: {result}"

    def test_unsafe_block_in_src_blocks(self, tmp_path):
        result = self._rust_scan(tmp_path,
            'pub fn deref(p: *const u8) -> u8 { unsafe { *p } }\n')
        assert any("unsafe" in b.lower() for b in result["blocks"]), \
            f"unsafe in src must block: {result}"

    def test_undocumented_pub_fn_warns(self, tmp_path):
        result = self._rust_scan(tmp_path,
            'pub fn process(v: &str) -> String { v.to_string() }\n')
        assert any("doc" in w.lower() for w in result["warnings"]), \
            f"undocumented public item must warn: {result['warnings']}"

    def test_documented_pub_fn_no_doc_warning(self, tmp_path):
        result = self._rust_scan(tmp_path,
            '/// Echo the input.\npub fn process(v: &str) -> String { v.to_string() }\n')
        assert not any("doc" in w.lower() for w in result["warnings"]), \
            f"documented public item must not warn: {result['warnings']}"

    def test_todo_macro_warns(self, tmp_path):
        result = self._rust_scan(tmp_path,
            '/// WIP.\npub fn process(v: &str) -> String { todo!() }\n')
        assert any("todo" in w.lower() for w in result["warnings"]), \
            f"todo! must warn: {result['warnings']}"


class TestJavaSpecific:
    """Checks unique to the Java engine's native scanner, including the
    comment/string/text-block awareness that regex can't do reliably."""

    def _java_scan(self, tmp_path, src_code, **kw):
        _skip_if_missing("java")
        return _scan(tmp_path, "java", "java", src_code, **kw)

    def test_toolchain_mismatch_detected(self):
        # Gradle 8.x on JDK 25 dies with "Unsupported class file major
        # version 69" - must surface as a toolchain error naming the JDK,
        # not as a widget content failure.
        from cartograph.languages.java import JavaEngine
        msg = JavaEngine._toolchain_mismatch(
            "BUG! exception in phase 'semantic analysis' in source unit "
            "'_BuildScript_' Unsupported class file major version 69")
        assert "Java 25" in msg
        assert "Gradle" in msg
        assert JavaEngine._toolchain_mismatch("Could not resolve gson") == ""
        assert JavaEngine._toolchain_mismatch("") == ""

    def test_println_in_src_blocks(self, tmp_path):
        result = self._java_scan(tmp_path,
            'public final class Module {\n'
            '    public static void hello() { System.out.println("debug"); }\n'
            '}\n')
        assert any("console output" in b for b in result["blocks"]), \
            f"System.out.println in src must block: {result}"

    def test_stderr_print_in_src_blocks(self, tmp_path):
        result = self._java_scan(tmp_path,
            'public final class Module {\n'
            '    public static void hello() { System.err.print("oops"); }\n'
            '}\n')
        assert any("console output" in b for b in result["blocks"]), \
            f"System.err.print in src must block: {result}"

    def test_println_in_string_not_blocked(self, tmp_path):
        result = self._java_scan(tmp_path,
            'public final class Module {\n'
            '    public static String doc() { return "call System.out.println(x)"; }\n'
            '}\n')
        assert not any("console output" in b for b in result["blocks"]), \
            f"println in a string must not block: {result['blocks']}"

    def test_println_in_text_block_not_blocked(self, tmp_path):
        result = self._java_scan(tmp_path,
            'public final class Module {\n'
            '    public static String doc() {\n'
            '        return """\n'
            '            System.out.println("x") and System.exit(1)\n'
            '            """;\n'
            '    }\n'
            '}\n')
        assert result["blocks"] == [], \
            f"contents of a text block must not block: {result['blocks']}"

    def test_println_in_comment_not_blocked(self, tmp_path):
        result = self._java_scan(tmp_path,
            '/** Call System.out.println(x) in your own code. */\n'
            'public final class Module {\n'
            '    private Module() {}\n'
            '    // System.exit(1) would end the process\n'
            '    /** Returns a greeting. */\n'
            '    public static String hello() { return "world"; }\n'
            '}\n')
        assert result["blocks"] == [], \
            f"print/exit in comments must not block: {result['blocks']}"

    def test_system_exit_in_src_blocks(self, tmp_path):
        result = self._java_scan(tmp_path,
            'public final class Module {\n'
            '    public static void quit() { System.exit(1); }\n'
            '}\n')
        assert any("exit" in b.lower() for b in result["blocks"]), \
            f"System.exit in src must block: {result}"

    def test_runtime_halt_in_src_blocks(self, tmp_path):
        result = self._java_scan(tmp_path,
            'public final class Module {\n'
            '    public static void quit() { Runtime.getRuntime().halt(1); }\n'
            '}\n')
        assert any("exit" in b.lower() for b in result["blocks"]), \
            f"Runtime.halt in src must block: {result}"

    def test_static_mutable_field_warns(self, tmp_path):
        result = self._java_scan(tmp_path,
            'public final class Module {\n'
            '    static int counter = 0;\n'
            '    private Module() {}\n'
            '}\n')
        assert any("static" in w.lower() for w in result["warnings"]), \
            f"non-final static field must warn: {result['warnings']}"

    def test_static_final_field_no_static_warning(self, tmp_path):
        result = self._java_scan(tmp_path,
            'public final class Module {\n'
            '    static final String NAME = "value";\n'
            '    private Module() {}\n'
            '}\n')
        assert not any("static" in w.lower() and "final" not in w
                       for w in result["warnings"]), \
            f"static final must not warn as mutable: {result['warnings']}"

    def test_print_in_example_not_flagged(self, tmp_path):
        _skip_if_missing("java")
        clean_src = CLEAN["java"][0]
        wdir = _make_widget(tmp_path, "java", "Module.java", clean_src)
        _write(os.path.join(wdir, "examples", "ExampleUsage.java"),
               'public final class ExampleUsage {\n'
               '    public static void main(String[] args) {\n'
               '        System.out.println("demo output");\n'
               '    }\n'
               '}\n')
        result = JavaEngine().scan_contamination(wdir, {})
        assert result["blocks"] == [], \
            f"examples demonstrate by printing - must not block: {result['blocks']}"


class TestGDScriptSpecific:
    """Checks unique to the GDScript engine - the Godot 3 -> 4 syntax blocks
    (the whole point of the engine) and the lexer's string/comment awareness."""

    def _gd_scan(self, tmp_path, src_code, **kw):
        _skip_if_missing("gdscript")
        return _scan(tmp_path, "gdscript", "gd", src_code, **kw)

    def test_godot3_onready_blocks(self, tmp_path):
        result = self._gd_scan(tmp_path, "onready var sprite = null\n")
        assert any("godot 3" in b.lower() for b in result["blocks"]), \
            f"onready var (Godot 3) must block: {result}"

    def test_godot3_export_paren_blocks(self, tmp_path):
        result = self._gd_scan(tmp_path, "export(int) var speed = 5\n")
        assert any("godot 3" in b.lower() for b in result["blocks"]), \
            f"export(...) (Godot 3) must block: {result}"

    def test_godot3_yield_blocks(self, tmp_path):
        result = self._gd_scan(tmp_path,
            'func go():\n\tyield(get_tree(), "idle_frame")\n')
        assert any("godot 3" in b.lower() for b in result["blocks"]), \
            f"yield (Godot 3) must block: {result}"

    def test_godot3_pool_array_blocks(self, tmp_path):
        result = self._gd_scan(tmp_path, "var data := PoolIntArray()\n")
        assert any("godot 3" in b.lower() for b in result["blocks"]), \
            f"Pool*Array (Godot 3) must block: {result}"

    def test_godot4_syntax_is_clean(self, tmp_path):
        result = self._gd_scan(tmp_path,
            "## A Godot 4 node.\n@export var speed: int = 5\n"
            "var data: PackedInt32Array = PackedInt32Array()\n")
        assert result["blocks"] == [], \
            f"Godot 4 syntax must not block: {result['blocks']}"

    def test_godot3_keyword_in_string_not_blocked(self, tmp_path):
        """Lexer awareness: a Godot 3 keyword inside a string must not block."""
        result = self._gd_scan(tmp_path,
            'func doc() -> String:\n\treturn "use yield() and onready in Godot 3"\n')
        assert result["blocks"] == [], \
            f"Godot 3 keyword in a string must not block: {result['blocks']}"

    def test_godot3_keyword_in_comment_not_blocked(self, tmp_path):
        result = self._gd_scan(tmp_path,
            "# onready and yield() were removed in Godot 4\n"
            "func ok() -> int:\n\treturn 1\n")
        assert result["blocks"] == [], \
            f"Godot 3 keyword in a comment must not block: {result['blocks']}"

    def test_print_in_src_blocks(self, tmp_path):
        result = self._gd_scan(tmp_path, 'func f() -> void:\n\tprint("debug")\n')
        assert any("print" in b.lower() for b in result["blocks"]), \
            f"print() in src must block: {result}"

    def test_absolute_node_path_blocks(self, tmp_path):
        # Bare $/... node-path syntax is code (not a string), so it trips the
        # scene-tree-coupling check specifically. A quoted "/root/..." path is
        # also blocked, but as an absolute-path literal.
        result = self._gd_scan(tmp_path,
            "func f() -> Node:\n\treturn $/root/Main/Player\n")
        assert any("node path" in b.lower() for b in result["blocks"]), \
            f"absolute node path must block: {result}"

    def test_untyped_var_warns(self, tmp_path):
        result = self._gd_scan(tmp_path, "var speed = 100\n")
        assert any("typed" in w.lower() for w in result["warnings"]), \
            f"untyped var should warn: {result['warnings']}"


class TestSpiceSpecific:
    """SPICE-only checks - .subckt-only src/, portability of includes,
    parameterization of component values."""

    def _spice_scan(self, tmp_path, src_code, dependencies=None):
        return _scan(tmp_path, "spice", "cir", src_code,
                     dependencies=dependencies)

    def test_clean_subckt_no_findings(self, tmp_path):
        result = self._spice_scan(tmp_path, _SUBCKT_CLEAN)
        assert result["blocks"] == [], f"clean block flagged: {result}"
        assert result["warnings"] == [], f"clean block warned: {result}"

    def test_missing_subckt_blocks(self, tmp_path):
        """src/ must define a reusable block, not a flat netlist."""
        result = self._spice_scan(tmp_path,
            "R1 in out 1k\nC1 out 0 159n\n")
        assert any("subckt" in b.lower() for b in result["blocks"]), \
            f"flat netlist in src/ must block: {result}"

    def test_analysis_card_in_src_blocks(self, tmp_path):
        """Analyses belong in the testbench, not the reusable block."""
        result = self._spice_scan(tmp_path,
            _SUBCKT_CLEAN + "\n.ac dec 10 1 1k\n")
        assert any("analysis card" in b.lower() and ".ac" in b
                   for b in result["blocks"]), \
            f"analysis card in src/ must block: {result}"

    def test_control_block_in_src_blocks(self, tmp_path):
        result = self._spice_scan(tmp_path,
            _SUBCKT_CLEAN + "\n.control\nop\n.endc\n")
        assert any(".control" in b for b in result["blocks"]), \
            f".control in src/ must block: {result}"

    def test_absolute_include_blocks(self, tmp_path):
        result = self._spice_scan(tmp_path,
            ".include /home/user/models/bjt.lib\n" + _SUBCKT_CLEAN)
        assert any("absolute path" in b.lower() for b in result["blocks"]), \
            f"absolute include must block: {result}"

    def test_undeclared_model_lib_blocks_in_src(self, tmp_path):
        result = self._spice_scan(tmp_path,
            ".lib mosfet_models.lib nom\n" + _SUBCKT_CLEAN)
        assert any("undeclared" in b.lower() and "model" in b.lower()
                   for b in result["blocks"]), \
            f"undeclared model lib must block in src/: {result}"

    def test_declared_model_lib_allowed(self, tmp_path):
        result = self._spice_scan(tmp_path,
            ".lib mosfet_models.lib nom\n" + _SUBCKT_CLEAN,
            dependencies=["mosfet_models>=1.0.0"])
        assert not any("undeclared" in b.lower() for b in result["blocks"]), \
            f"declared model lib must not block: {result['blocks']}"

    def test_hardcoded_value_in_subckt_warns(self, tmp_path):
        result = self._spice_scan(tmp_path,
            ".subckt lp in out\nR1 in out 1k\nC1 out 0 {c}\n.ends\n")
        assert any("hardcoded value" in w.lower() and "R1" in w
                   for w in result["warnings"]), \
            f"hardcoded device value must warn: {result['warnings']}"

    def test_parameterized_value_not_flagged(self, tmp_path):
        result = self._spice_scan(tmp_path, _SUBCKT_CLEAN)
        assert not any("hardcoded value" in w.lower()
                       for w in result["warnings"]), \
            f"{{param}} values must not warn: {result['warnings']}"

    def test_credential_blocks(self, tmp_path):
        result = self._spice_scan(tmp_path,
            _SUBCKT_CLEAN + '\n* token = "sk-abc123verylongkey"\n'
            'Vsupply n1 0 token="sk-abc123verylongkey"\n')
        assert any("credential" in b.lower() for b in result["blocks"]), \
            f"credential must block: {result}"

    def test_credential_in_comment_not_blocked(self, tmp_path):
        """Inline ';' and full-line '*' comments are stripped before scanning."""
        result = self._spice_scan(tmp_path,
            _SUBCKT_CLEAN + '\n* password = "hunter2-do-not-use"\n'
            'R2 a b {r}  ; secret = "do-not-flag-this-comment"\n')
        assert not any("credential" in b.lower() for b in result["blocks"]), \
            f"credential in comment must not block: {result['blocks']}"


# ---------------------------------------------------------------------------
# macOS filesystem cruft and unparseable sources
# ---------------------------------------------------------------------------

class TestMacosCruft:
    def test_appledouble_file_in_src_is_ignored(self, tmp_path):
        wdir = _make_widget(tmp_path, "python", "mod.py", "def f():\n    return 1\n")
        with open(os.path.join(wdir, "src", "._mod.py"), "wb") as f:
            f.write(b"\x00\x05\x16\x07binary resource fork")
        result = PythonEngine().scan_contamination(
            wdir, {"language": "python", "dependencies": []})
        assert result["blocks"] == []

    def test_unparseable_src_file_blocks(self, tmp_path):
        wdir = _make_widget(tmp_path, "python", "mod.py", "def broken(:\n")
        result = PythonEngine().scan_contamination(
            wdir, {"language": "python", "dependencies": []})
        assert any("not valid Python" in b for b in result["blocks"]), result

    def test_unparseable_test_file_does_not_block(self, tmp_path):
        wdir = _make_widget(tmp_path, "python", "mod.py",
                            "def f():\n    return 1\n",
                            test_code="def broken(:\n")
        result = PythonEngine().scan_contamination(
            wdir, {"language": "python", "dependencies": []})
        assert not any("not valid Python" in b for b in result["blocks"])


class TestJavaStubDetection:
    def test_check_available_rejects_non_working_java(self, monkeypatch):
        """macOS's /usr/bin/java stub resolves on PATH but can't run."""
        engine = JavaEngine()
        monkeypatch.setattr(JavaEngine, "_java_probe_ok", None)
        monkeypatch.setattr(JavaEngine, "runtime_version", lambda self: None)
        monkeypatch.setattr(type(engine).__mro__[1], "check_available",
                            lambda self: (True, ""))
        ok, msg = engine.check_available()
        assert ok is False
        assert "JDK" in msg

    def test_check_available_accepts_working_java(self, monkeypatch):
        engine = JavaEngine()
        monkeypatch.setattr(JavaEngine, "_java_probe_ok", None)
        monkeypatch.setattr(JavaEngine, "runtime_version",
                            lambda self: "java 21.0.1")
        monkeypatch.setattr(type(engine).__mro__[1], "check_available",
                            lambda self: (True, ""))
        ok, msg = engine.check_available()
        assert ok is True


# ---------------------------------------------------------------------------
# Lean-specific checks
# ---------------------------------------------------------------------------

class TestLeanSpecific:
    """Lean's headline guarantee: checked-in means proven. The compiler only
    warns on `sorry` and silently accepts `axiom`, so the scanner must hard-
    block both - and its lexer must not trip on the same tokens inside
    strings, line comments, or (nested) block comments."""

    def _lean_scan(self, tmp_path, src, test_code=""):
        return _scan(tmp_path, "lean", "lean", src, test_code)

    def test_sorry_in_src_blocks(self, tmp_path):
        _skip_if_missing("lean")
        result = self._lean_scan(
            tmp_path, "theorem t (n : Nat) : n = n := by sorry\n")
        assert any("sorry" in b.lower() for b in result["blocks"]), result

    def test_sorry_in_tests_blocks(self, tmp_path):
        # Unlike credentials/IPs, an unproven test is a failed validation,
        # not fake data - sorry blocks everywhere.
        _skip_if_missing("lean")
        result = self._lean_scan(
            tmp_path, CLEAN["lean"][0],
            test_code="example : 1 = 1 := by sorry\n")
        assert any("sorry" in b.lower() for b in result["blocks"]), result

    def test_admit_blocks(self, tmp_path):
        _skip_if_missing("lean")
        result = self._lean_scan(
            tmp_path, "theorem t (n : Nat) : n = n := by admit\n")
        assert any("sorry" in b.lower() or "admit" in b.lower()
                   for b in result["blocks"]), result

    def test_axiom_in_src_blocks(self, tmp_path):
        _skip_if_missing("lean")
        result = self._lean_scan(tmp_path, "axiom cheat : 1 = 2\n")
        assert any("axiom" in b.lower() for b in result["blocks"]), result

    def test_println_in_src_blocks(self, tmp_path):
        _skip_if_missing("lean")
        result = self._lean_scan(
            tmp_path, 'def log : IO Unit := IO.println "hi"\n')
        assert any("console output" in b.lower()
                   for b in result["blocks"]), result

    def test_partial_def_warns(self, tmp_path):
        _skip_if_missing("lean")
        result = self._lean_scan(
            tmp_path, "partial def spin (n : Nat) : Nat := spin n\n")
        assert any("partial" in w.lower() for w in result["warnings"]), result

    def test_native_decide_warns(self, tmp_path):
        _skip_if_missing("lean")
        result = self._lean_scan(
            tmp_path,
            "theorem t : 2 + 2 = 4 := by native_decide\n")
        assert any("native_decide" in w.lower()
                   for w in result["warnings"]), result

    # -- lexer negatives: banned tokens in strings/comments must NOT trip --

    def test_sorry_in_string_clean(self, tmp_path):
        _skip_if_missing("lean")
        result = self._lean_scan(
            tmp_path, 'def msg : String := "we are sorry about that"\n')
        assert result["blocks"] == [], result

    def test_sorry_in_line_comment_clean(self, tmp_path):
        _skip_if_missing("lean")
        result = self._lean_scan(
            tmp_path,
            "-- sorry axiom IO.println all fine here\n"
            'def hello : String := "world"\n')
        assert result["blocks"] == [], result

    def test_sorry_in_nested_block_comment_clean(self, tmp_path):
        _skip_if_missing("lean")
        result = self._lean_scan(
            tmp_path,
            "/- outer sorry /- nested axiom -/ still comment sorry -/\n"
            'def hello : String := "world"\n')
        assert result["blocks"] == [], result

    def test_doc_comment_clean(self, tmp_path):
        _skip_if_missing("lean")
        result = self._lean_scan(
            tmp_path,
            "/-- Doc mentioning sorry, axiom, IO.println. -/\n"
            'def hello : String := "world"\n')
        assert result["blocks"] == [], result


@pytest.mark.csharp
class TestCSharpSpecific:
    """Checks unique to the C# engine's native scanner, including the
    comment/string/verbatim/raw-string awareness regex can't do reliably."""

    def _cs_scan(self, tmp_path, src_code, **kw):
        _skip_if_missing("csharp")
        return _scan(tmp_path, "csharp", "cs", src_code, **kw)

    def test_console_write_in_src_blocks(self, tmp_path):
        result = self._cs_scan(tmp_path,
            'public static class Module\n'
            '{\n'
            '    public static void Hello() { Console.WriteLine("debug"); }\n'
            '}\n')
        assert any("console output" in b for b in result["blocks"]), \
            f"Console.WriteLine in src must block: {result}"

    def test_console_error_in_src_blocks(self, tmp_path):
        result = self._cs_scan(tmp_path,
            'public static class Module\n'
            '{\n'
            '    public static void Hello() { Console.Error.Write("oops"); }\n'
            '}\n')
        assert any("console output" in b for b in result["blocks"]), \
            f"Console.Error.Write in src must block: {result}"

    def test_environment_exit_in_src_blocks(self, tmp_path):
        result = self._cs_scan(tmp_path,
            'public static class Module\n'
            '{\n'
            '    public static void Die() { Environment.Exit(1); }\n'
            '}\n')
        assert any("exit" in b.lower() for b in result["blocks"]), \
            f"Environment.Exit in src must block: {result}"

    def test_console_write_in_string_not_flagged(self, tmp_path):
        result = self._cs_scan(tmp_path,
            'public static class Module\n'
            '{\n'
            '    public static string Doc() { return '
            '"call Console.WriteLine(x) to print"; }\n'
            '}\n')
        assert result["blocks"] == [], \
            f"Console.WriteLine inside a string must not trip: {result}"

    def test_console_write_in_comment_not_flagged(self, tmp_path):
        result = self._cs_scan(tmp_path,
            '/// <summary>Never call Console.WriteLine("x") here.</summary>\n'
            'public static class Module\n'
            '{\n'
            '    // Console.WriteLine("also fine in a comment");\n'
            '    /* Environment.Exit(1) in a block comment */\n'
            '    public static string Hello() { return "world"; }\n'
            '}\n')
        assert result["blocks"] == [], \
            f"banned tokens in comments must not trip: {result}"

    def test_console_write_in_verbatim_string_not_flagged(self, tmp_path):
        result = self._cs_scan(tmp_path,
            'public static class Module\n'
            '{\n'
            '    public static string Doc() { return '
            '@"use Console.WriteLine(""x"") like this"; }\n'
            '}\n')
        assert result["blocks"] == [], \
            f"verbatim string contents must not trip: {result}"

    def test_console_write_in_raw_string_not_flagged(self, tmp_path):
        result = self._cs_scan(tmp_path,
            'public static class Module\n'
            '{\n'
            '    public static string Doc() { return """\n'
            '        Console.WriteLine("x") and Thread.Sleep(99) are words\n'
            '        """; }\n'
            '}\n')
        assert result["blocks"] == [], \
            f"raw string contents must not trip: {result}"

    def test_url_not_flagged_as_abs_path(self, tmp_path):
        # "https://..." must not match the drive-letter branch of the
        # abs-path pattern via "s://" - it is a URL warning, never a block.
        result = self._cs_scan(tmp_path,
            'public static class Module\n'
            '{\n'
            '    public static string Api() '
            '{ return "https://api.mycompany.com/v1"; }\n'
            '}\n')
        assert not any("path" in b.lower() for b in result["blocks"]), \
            f"URL must not block as an absolute path: {result['blocks']}"

    def test_windows_drive_path_blocks(self, tmp_path):
        result = self._cs_scan(tmp_path,
            'public static class Module\n'
            '{\n'
            '    public static string P() { return @"C:\\Users\\ben\\data"; }\n'
            '}\n')
        assert any("path" in b.lower() for b in result["blocks"]), \
            f"drive-letter path must block: {result}"

    def test_mutable_static_warns_readonly_does_not(self, tmp_path):
        result = self._cs_scan(tmp_path,
            'public static class Module\n'
            '{\n'
            '    public static int counter = 0;\n'
            '    public static readonly int Limit = 1;\n'
            '}\n')
        joined = "\n".join(result["warnings"])
        assert "counter" in joined, \
            f"mutable static field must warn: {result}"
        assert "Limit" not in joined, \
            f"static readonly must not warn: {result}"

    def test_sleep_non_literal_in_test_warns(self, tmp_path):
        clean_src = CLEAN["csharp"][0]
        result = self._cs_scan(tmp_path, clean_src,
            test_code='public class ModuleTests\n'
                      '{\n'
                      '    public void Pause(int ms) { Thread.Sleep(ms); }\n'
                      '}\n')
        assert any("sleep" in w.lower() for w in result["warnings"]), \
            f"non-literal sleep in tests must warn (unknown duration): {result}"


# ---------------------------------------------------------------------------
# Flutter-specific validation
# ---------------------------------------------------------------------------

class TestFlutterSpecific:
    """Checks unique to the Flutter engine's native Dart scanner, including
    the comment/string/raw/triple-quote awareness regex can't do reliably
    (Dart block comments nest; raw strings have no escapes)."""

    def _fl_scan(self, tmp_path, src_code, **kw):
        _skip_if_missing("flutter")
        return _scan(tmp_path, "flutter", "dart", src_code, **kw)

    def test_print_in_src_blocks(self, tmp_path):
        result = self._fl_scan(tmp_path,
            "void hello() { print('debug'); }\n")
        assert any("console output" in b for b in result["blocks"]), \
            f"print in src must block: {result}"

    def test_debugprint_in_src_blocks(self, tmp_path):
        result = self._fl_scan(tmp_path,
            "import 'package:flutter/foundation.dart';\n\n"
            "void hello() { debugPrint('debug'); }\n")
        assert any("console output" in b for b in result["blocks"]), \
            f"debugPrint in src must block: {result}"

    def test_stdout_write_in_src_blocks(self, tmp_path):
        result = self._fl_scan(tmp_path,
            "import 'dart:io';\n\n"
            "void hello() { stdout.writeln('debug'); }\n")
        assert any("console output" in b for b in result["blocks"]), \
            f"stdout.writeln in src must block: {result}"

    def test_exit_in_src_blocks(self, tmp_path):
        result = self._fl_scan(tmp_path,
            "import 'dart:io';\n\n"
            "void bail() { exit(1); }\n")
        assert any("exit()" in b for b in result["blocks"]), \
            f"exit() in src must block: {result}"

    def test_print_in_tests_allowed(self, tmp_path):
        clean_src = CLEAN["flutter"][0]
        result = self._fl_scan(tmp_path, clean_src,
            test_code="void main() { print('test debug'); }\n")
        assert not result["blocks"], \
            f"print in tests must not block: {result['blocks']}"

    def test_banned_tokens_in_strings_do_not_trip(self, tmp_path):
        result = self._fl_scan(tmp_path,
            "String a() => 'call print( here';\n"
            "String b() => r'raw with sleep( inside';\n"
            "String c() => '''triple with exit( inside''';\n"
            'String d() => "double with debugPrint( inside";\n')
        assert not result["blocks"], \
            f"banned tokens inside strings must not trip: {result['blocks']}"

    def test_banned_tokens_in_comments_do_not_trip(self, tmp_path):
        result = self._fl_scan(tmp_path,
            "// print('in a line comment')\n"
            "/* exit(1) in a block comment\n"
            "   /* sleep(Duration(seconds: 9)) in a NESTED comment */\n"
            "   still inside the outer comment: debugPrint('x')\n"
            "*/\n"
            "String hello() => 'world';\n")
        assert not result["blocks"] and not result["warnings"], \
            f"banned tokens inside (nested) comments must not trip: {result}"

    def test_flutter_sdk_imports_not_flagged(self, tmp_path):
        result = self._fl_scan(tmp_path,
            "import 'package:flutter/widgets.dart';\n\n"
            "Widget hello() => const Text('hi', "
            "textDirection: TextDirection.ltr);\n")
        assert not any("Unlisted" in w for w in result["warnings"]), \
            f"package:flutter must not need declaring: {result['warnings']}"

    def test_relative_import_not_flagged(self, tmp_path):
        result = self._fl_scan(tmp_path,
            "import 'other_helper.dart';\n\n"
            "String hello() => 'world';\n")
        assert not any("Unlisted" in w for w in result["warnings"]), \
            f"relative imports must not need declaring: {result['warnings']}"

    def test_pubspec_path_dep_import_not_flagged(self, tmp_path):
        # Blueprint-composed widgets are declared as pub path deps in
        # pubspec.yaml (not widget.json) - the scanner must honor them.
        _skip_if_missing("flutter")
        _write(os.path.join(str(tmp_path), "pubspec.yaml"),
               "name: shouter\n"
               "dependencies:\n"
               "  greet: { path: cg/universal-greet-flutter }\n")
        result = self._fl_scan(tmp_path,
            "import 'package:greet/greet.dart';\n\n"
            "String shout(String n) => greet(n).toUpperCase();\n")
        assert not any("Unlisted" in w for w in result["warnings"]), \
            f"pubspec path deps must count as declared: {result['warnings']}"

    def test_sleep_non_literal_in_test_warns(self, tmp_path):
        clean_src = CLEAN["flutter"][0]
        result = self._fl_scan(tmp_path, clean_src,
            test_code="import 'dart:io';\n\n"
                      "void pause(Duration d) { sleep(d); }\n")
        assert any("sleep" in w.lower() for w in result["warnings"]), \
            f"non-literal sleep in tests must warn (unknown duration): {result}"
