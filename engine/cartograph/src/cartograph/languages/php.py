"""PHP language engine - uses Composer + PHPUnit + Xdebug/PCOV."""

import glob
import json
import os
import re
import shutil

from .base import LanguageEngine, _dep_bare_name, log

_COVERAGE_THRESHOLD = 80

# ── Scaffold templates ────────────────────────────────────────────────────────

_SRC_TEMPLATE = '''\
<?php

declare(strict_types=1);

namespace Cartograph\\__NAMESPACE__;

/**
 * __DISPLAY__ - pure PHP utility, no framework dependencies.
 *
 * No project-specific names, identifiers, or terminology in src/.
 * Use generic names: item, value, result, input, output.
 */
class __CLASS__
{
    /**
     * Process a value and return a result.
     *
     * @param mixed $value Input value to process.
     * @return mixed Processed result.
     */
    public function process(mixed $value): mixed
    {
        return $value;
    }
}
'''

_TEST_TEMPLATE = '''\
<?php

declare(strict_types=1);

use PHPUnit\\Framework\\TestCase;
use Cartograph\\__NAMESPACE__\\__CLASS__;

class __CLASS__Test extends TestCase
{
    private __CLASS__ $subject;

    protected function setUp(): void
    {
        $this->subject = new __CLASS__();
    }

    public function test_process_returns_value(): void
    {
        $result = $this->subject->process('hello');
        $this->assertSame('hello', $result);
    }
}
'''

_EXAMPLE_TEMPLATE = '''\
<?php

/**
 * Example usage of __DISPLAY__.
 *
 * This file must run and exit cleanly with no user input, no network calls,
 * and no external services or API keys. Use fake/hardcoded data to demonstrate
 * the API. The widget\'s own declared dependencies are fine - the validator
 * installs them first.
 */

declare(strict_types=1);

require_once __DIR__ . \'/../vendor/autoload.php\';

use Cartograph\\__NAMESPACE__\\__CLASS__;

$instance = new __CLASS__();

// [TODO] Replace with a realistic call using fake data
$result = $instance->process(\'example input\');
echo "Result: " . print_r($result, true) . PHP_EOL;
'''

_COMPOSER_JSON = '''\
{
    "name": "cartograph/__SLUG__",
    "description": "[TODO] Describe what __DISPLAY__ does",
    "type": "library",
    "require": {
        "php": ">=8.1"
    },
    "require-dev": {
        "phpunit/phpunit": "^11.0"
    },
    "autoload": {
        "psr-4": {
            "Cartograph\\\\__NAMESPACE__\\\\": "src/"
        }
    },
    "autoload-dev": {
        "psr-4": {
            "Cartograph\\\\__NAMESPACE__\\\\": "tests/"
        }
    }
}
'''

_PHPUNIT_XML = '''\
<?xml version="1.0" encoding="UTF-8"?>
<phpunit xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:noNamespaceSchemaLocation="vendor/phpunit/phpunit/phpunit.xsd"
         bootstrap="vendor/autoload.php"
         colors="true"
         stopOnFailure="false">
    <testsuites>
        <testsuite name="Widget Tests">
            <directory>tests</directory>
        </testsuite>
    </testsuites>
    <source>
        <include>
            <directory>src</directory>
        </include>
    </source>
    <coverage>
        <report>
            <text outputFile="php://stdout" showUncoveredFiles="false"/>
        </report>
    </coverage>
</phpunit>
'''


def _to_class_name(module_name: str) -> str:
    """Convert module_name (snake_case or kebab) to PascalCase class name."""
    return "".join(part.capitalize() for part in re.split(r"[-_]", module_name))


def _to_namespace(module_name: str) -> str:
    """Convert module_name to a valid PHP namespace segment (PascalCase)."""
    return _to_class_name(module_name)


def _to_slug(module_name: str) -> str:
    """Lowercase hyphenated slug for composer.json name."""
    return module_name.replace("_", "-").lower()


class PhpEngine(LanguageEngine):
    name = "php"
    aliases = []
    validation_version = 1
    file_ext = "php"
    supported = True
    env_dir = "vendor"

    def lock_patterns(self, path: str) -> list:
        pats = []
        for f in ("composer.lock", "composer.json"):
            fp = os.path.join(path, f)
            if os.path.exists(fp):
                pats.append(fp)
        return pats

    def verify_installed(self, path: str, dependencies: list) -> bool:
        from ..dep_cache import parse_requirement, satisfies
        installed_json = os.path.join(path, "vendor", "composer", "installed.json")
        if not os.path.isfile(installed_json):
            return False
        try:
            with open(installed_json, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        # Composer 2 wraps the list in {"packages": [...]}; v1 is a bare list.
        pkgs = data.get("packages", []) if isinstance(data, dict) else data
        versions = {}
        for p in pkgs:
            nm = p.get("name")
            if nm:
                versions[nm.lower()] = (p.get("version") or "").lstrip("vV")
        for dep in dependencies:
            name, spec = parse_requirement(dep)
            if not name:
                continue
            ver = versions.get(name.lower())
            if ver is None:
                return False
            if satisfies(ver, spec) is False:
                return False
        return True

    toolchain = {
        "php": "Install PHP 8.1+ - php.net/downloads",
        "composer": "Install Composer - getcomposer.org",
    }

    # Native scanner wired up below after scanner is built.
    # scanner_runner and scanner_messages set when php_scanner.php exists.

    def runtime_version(self) -> str | None:
        res = self._run(["php", "--version"], cwd=os.getcwd(), timeout=10)
        if res.returncode != 0:
            return None
        first_line = (res.stdout or "").splitlines()[0]
        # "PHP 8.2.10 (cli) ..." -> "php 8.2.10"
        m = re.match(r"PHP\s+(\S+)", first_line)
        return f"php {m.group(1)}" if m else first_line.strip()

    def check_optional(self) -> list[tuple[str, bool, str]]:
        """Surface Xdebug / PCOV coverage driver status in doctor."""
        res = self._run(
            ["php", "-r", "echo extension_loaded('xdebug') ? 'yes' : 'no';"],
            cwd=os.getcwd(), timeout=10,
        )
        xdebug_ok = res.returncode == 0 and "yes" in (res.stdout or "")

        res2 = self._run(
            ["php", "-r", "echo extension_loaded('pcov') ? 'yes' : 'no';"],
            cwd=os.getcwd(), timeout=10,
        )
        pcov_ok = res2.returncode == 0 and "yes" in (res2.stdout or "")

        if xdebug_ok:
            return [("xdebug", True, "coverage driver available")]
        if pcov_ok:
            return [("pcov", True, "coverage driver available")]
        return [(
            "xdebug/pcov", False,
            "no coverage driver - install Xdebug or PCOV: sudo dnf install php-pecl-xdebug3",
        )]

    # ── Scaffold ──────────────────────────────────────────────────────────────

    def scaffold(self, target_dir: str, module_name: str, display_name: str, **_) -> None:
        class_name = _to_class_name(module_name)
        namespace = _to_namespace(module_name)
        slug = _to_slug(module_name)

        def sub(template: str) -> str:
            return (template
                    .replace("__CLASS__", class_name)
                    .replace("__NAMESPACE__", namespace)
                    .replace("__DISPLAY__", display_name)
                    .replace("__SLUG__", slug))

        with open(os.path.join(target_dir, "src", f"{class_name}.php"), "w", encoding="utf-8") as f:
            f.write(sub(_SRC_TEMPLATE))

        with open(os.path.join(target_dir, "tests", f"{class_name}Test.php"), "w", encoding="utf-8") as f:
            f.write(sub(_TEST_TEMPLATE))

        with open(os.path.join(target_dir, "examples", "example_usage.php"), "w", encoding="utf-8") as f:
            f.write(sub(_EXAMPLE_TEMPLATE))

        with open(os.path.join(target_dir, "composer.json"), "w", encoding="utf-8") as f:
            f.write(sub(_COMPOSER_JSON))

        with open(os.path.join(target_dir, "phpunit.xml"), "w", encoding="utf-8") as f:
            f.write(_PHPUNIT_XML)

    # ── Contamination ─────────────────────────────────────────────────────────

    # WordPress globals that make code untestable outside WP environment.
    # Two parts: word-char function names need \b; $-prefixed variables don't
    # ($ is not a word char so \b before $ never fires)
    _WP_GLOBALS_RE = re.compile(
        r'(?:\b(?:wp_[a-zA-Z_]+|add_action|add_filter|do_action|apply_filters'
        r'|register_post_type|register_taxonomy|get_option|update_option'
        r'|delete_option|get_post|get_posts|get_the_ID|the_content)\b'
        r'|(?:\$wpdb|\$wp_query|\$post|\$current_user)\b)'
    )
    _ABS_PATH_RE = re.compile(
        r'["\'](?:/home/|/Users/|/root/|[A-Za-z]:[/\\\\])[^"\']{3,}["\']'
    )
    _CREDENTIAL_RE = re.compile(
        r'(?:api_key|api_secret|secret_key|access_token|auth_token|password|passwd|credential)\s*=\s*["\'][^"\']{6,}["\']',
        re.IGNORECASE,
    )
    _URL_RE = re.compile(
        r'["\']https?://(?!(?:localhost|127\.0\.0\.1|(?:[\w-]+\.)*example\.com'
        r'|[\w.-]+\.test(?:[/:"\'#?]|$)))[^"\']{8,}["\']'
    )
    _IP_RE = re.compile(r'["\'](?:\d{1,3}\.){3}\d{1,3}(?::\d+)?["\']')
    _SLEEP_RE = re.compile(r'\bsleep\s*\(|\busleep\s*\(')
    _ECHO_RE = re.compile(r'^\s*echo\s+', re.MULTILINE)
    _HARDCODED_RE = re.compile(
        r'(?:private|protected|public|static|const|\$)\s+\$?[A-Z_]{3,}\s*=\s*["\'\d]'
    )
    _ENVVAR_RE = re.compile(r'\bgetenv\s*\(|\$_ENV\b|\$_SERVER\b')

    def _parse_autoload_namespaces(self, autoload_dir: str) -> set:
        """Read composer's autoload_*.php files and return the lowercase set
        of namespace roots that composer can resolve. This is the same source
        PHP's runtime autoloader uses, so anything not in this set genuinely
        cannot be `use`-d at runtime."""
        roots = set()
        # PSR-4 and PSR-0 keys look like: 'Carbon\\' => array(...)
        psr_re = re.compile(r"'([A-Za-z_][A-Za-z0-9_\\]*?)\\\\'\s*=>")
        for name in ("autoload_psr4.php", "autoload_namespaces.php"):
            p = os.path.join(autoload_dir, name)
            if not os.path.exists(p):
                continue
            try:
                content = open(p, encoding="utf-8").read()
            except Exception:
                continue
            for m in psr_re.finditer(content):
                key = m.group(1).replace("\\\\", "\\")
                root = key.split("\\")[0].lower()
                if root:
                    roots.add(root)
        # classmap: flat 'Fully\\Qualified\\Class' => '/path/to/file.php'
        cm = os.path.join(autoload_dir, "autoload_classmap.php")
        if os.path.exists(cm):
            try:
                content = open(cm, encoding="utf-8").read()
                fq_re = re.compile(r"'([A-Za-z_][A-Za-z0-9_]*(?:\\\\[A-Za-z_][A-Za-z0-9_]*)+)'\s*=>")
                for m in fq_re.finditer(content):
                    key = m.group(1).replace("\\\\", "\\")
                    root = key.split("\\")[0].lower()
                    if root:
                        roots.add(root)
            except Exception:
                pass
        return roots

    def _resolve_provided_namespaces(self, path: str, deps: list) -> set | None:
        """Build the ground-truth set of PHP namespace roots this widget can
        actually resolve. Returns None if the autoload table is unavailable
        (e.g. composer install failed), which signals callers to fall back
        to the heuristic path."""
        autoload_dir = os.path.join(path, "vendor", "composer")
        autoload_file = os.path.join(autoload_dir, "autoload_psr4.php")
        if not os.path.exists(autoload_file):
            # Need composer install to produce the autoload table. Skip if
            # there's nothing to install against - no composer.json or no
            # deps means we have no ground truth to build.
            if not os.path.exists(os.path.join(path, "composer.json")):
                return None
            try:
                self.install_deps(path, deps)
            except Exception as e:
                log.warning(f"PHP contamination: composer install failed, "
                            f"falling back to heuristic resolver: {e}")
                return None
        if not os.path.isdir(autoload_dir):
            return None
        return self._parse_autoload_namespaces(autoload_dir)

    def scan_contamination(self, path: str, widget: dict) -> dict:
        blocks, warnings = [], []

        deps = widget.get("dependencies", [])
        dep_names = set()  # fallback heuristic if resolver unavailable
        for d in deps:
            if not isinstance(d, str):
                continue
            bare = _dep_bare_name(d).lower()
            dep_names.add(bare)
            if "/" in bare:
                dep_names.add(bare.split("/")[0])

        # Authoritative namespace resolution via composer's autoload tables.
        provided = self._resolve_provided_namespaces(path, deps)

        src_files = glob.glob(os.path.join(path, "src", "**", "*.php"), recursive=True)
        test_files = glob.glob(os.path.join(path, "tests", "**", "*.php"), recursive=True)
        example_files = glob.glob(os.path.join(path, "examples", "**", "*.php"), recursive=True)

        all_files = [
            (f, True, False) for f in src_files
        ] + [
            (f, False, False) for f in test_files
        ] + [
            (f, False, True) for f in example_files
        ]

        for fpath, is_src, is_example in all_files:
            rel = os.path.relpath(fpath, path)
            try:
                code = open(fpath, encoding="utf-8").read()
            except Exception as e:
                blocks.append(f"Could not read {rel}: {e}")
                continue

            lines = code.splitlines()
            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                loc = f"{rel}:{line_no}"
                # Skip single-line comments
                is_comment = stripped.startswith("//") or stripped.startswith("#") or stripped.startswith("*")

                if not is_comment:
                    # Absolute paths - all files
                    if self._ABS_PATH_RE.search(line):
                        blocks.append(f"Absolute path in {loc}: {stripped[:120]}")

                    # Credentials - block in src, warn in tests
                    if self._CREDENTIAL_RE.search(line):
                        if is_src:
                            blocks.append(f"Possible credential in {loc}: {stripped[:120]}")
                        else:
                            warnings.append(f"Possible credential in {loc} - verify it's fake: {stripped[:120]}")

                    # WordPress globals - block everywhere
                    if self._WP_GLOBALS_RE.search(line):
                        blocks.append(
                            f"WordPress global in {loc}: {stripped[:120]} - "
                            f"widgets must be pure PHP, no WordPress dependencies"
                        )

                    # echo in src - same rule as Python's print()
                    if is_src and re.match(r'^\s*echo\s+', line):
                        blocks.append(f"echo in {loc} - remove debug output from src/")

                    # sleep in src
                    if is_src and self._SLEEP_RE.search(line):
                        blocks.append(f"sleep() in {loc} - widgets must not block the caller")

                    # sleep in tests/examples: warn if large value
                    if not is_src and self._SLEEP_RE.search(line):
                        m = re.search(r'(?:sleep|usleep)\s*\(\s*(\d+)', line)
                        if m:
                            val = int(m.group(1))
                            # usleep is microseconds; sleep is seconds
                            seconds = val / 1_000_000 if "usleep" in line else val
                            if seconds > 1:
                                warnings.append(
                                    f"sleep({val}) in {loc} - consider reducing sleep duration"
                                )

                    # Hardcoded URLs - src only
                    if is_src:
                        for m in self._URL_RE.finditer(line):
                            warnings.append(f"Hardcoded URL in {loc}: {m.group()}")

                    # Hardcoded IPs - src only
                    if is_src:
                        for m in self._IP_RE.finditer(line):
                            octets = m.group().strip("\"'").split(":")[0].split(".")
                            if any(len(o) >= 2 for o in octets):
                                blocks.append(f"Hardcoded IP in {loc}: {m.group()}")

                    # env vars - src only
                    if is_src and self._ENVVAR_RE.search(line):
                        warnings.append(f"getenv/env access in {loc} - verify it's not project-specific")

            # Hardcoded constant assignments in src (class-level consts/properties)
            if is_src:
                for m in self._HARDCODED_RE.finditer(code):
                    line_no = code[:m.start()].count("\n") + 1
                    warnings.append(
                        f"Hardcoded value in {rel}:{line_no}: {m.group()[:80]} - "
                        f"consider making this a parameter"
                    )

            # Unlisted `use` statements. Block in src/, warn in tests/examples.
            # Authoritative resolver: composer's autoload tables. Fallback:
            # vendor-prefix heuristic (warning only - can't confidently block
            # when we don't know the ground truth).
            for m in re.finditer(
                r'^\s*use\s+(?:function\s+|const\s+)?([A-Za-z_][A-Za-z0-9_\\]*)',
                code, re.MULTILINE,
            ):
                ns_path = m.group(1)
                line_no = code[:m.start()].count("\n") + 1
                loc = f"{rel}:{line_no}"
                top = ns_path.split("\\")[0].lower()
                if not top:
                    continue
                if provided is not None:
                    if top not in provided:
                        msg = (f"Unlisted namespace '{top}' in {loc} - "
                               f"not resolvable via composer autoload; "
                               f"add to dependencies or remove")
                        (blocks if is_src else warnings).append(msg)
                else:
                    # Resolver unavailable - conservative heuristic
                    if top not in dep_names and top not in {
                        "php", "psr", "phpunit", "cartograph",
                    }:
                        warnings.append(
                            f"Unlisted import '{top}' in {loc} - "
                            f"add to dependencies or remove "
                            f"(resolver unavailable)"
                        )

        return {"blocks": blocks, "warnings": warnings}

    # ── Install ───────────────────────────────────────────────────────────────

    def install_deps(self, path: str, dependencies: list) -> None:
        if self._deps_cached(path, dependencies):
            log.debug("Reusing cached vendor/ at %s", path)
            return
        composer_path = os.path.join(path, "composer.json")
        if os.path.exists(composer_path) and dependencies:
            with open(composer_path, encoding="utf-8") as f:
                pkg = json.load(f)
            changed = False
            for dep in dependencies:
                bare = _dep_bare_name(dep)
                ver_part = dep[len(bare):].strip() or "*"
                already = (
                    bare in pkg.get("require", {})
                    or bare in pkg.get("require-dev", {})
                )
                if bare and not already:
                    pkg.setdefault("require", {})[bare] = ver_part
                    changed = True
            if changed:
                with open(composer_path, "w", encoding="utf-8") as f:
                    json.dump(pkg, f, indent=4)
                    f.write("\n")

        res = self._run(
            ["composer", "install", "--no-interaction", "--prefer-dist", "--quiet"],
            cwd=path,
            timeout=120,
        )
        if res.returncode != 0:
            output = (res.stderr or res.stdout or "").strip()
            raise RuntimeError(
                "Failed to install Composer dependencies."
                + (f"\n{output[:2000]}" if output else "")
            )

        self._record_dep_cache(path, dependencies)

    # ── Tests ─────────────────────────────────────────────────────────────────

    def run_tests(self, path: str) -> dict:
        # Detect available coverage driver
        xdebug = self._run(
            ["php", "-r", "echo extension_loaded('xdebug') ? 'yes' : 'no';"],
            cwd=path, timeout=10,
        )
        pcov = self._run(
            ["php", "-r", "echo extension_loaded('pcov') ? 'yes' : 'no';"],
            cwd=path, timeout=10,
        )
        has_xdebug = xdebug.returncode == 0 and "yes" in (xdebug.stdout or "")
        has_pcov = pcov.returncode == 0 and "yes" in (pcov.stdout or "")

        if not has_xdebug and not has_pcov:
            return self._fail(
                "No PHP coverage driver found. Install Xdebug or PCOV to enable "
                "coverage enforcement.\n"
                "  Xdebug: sudo dnf install php-pecl-xdebug3\n"
                "  PCOV:   pecl install pcov"
            )

        # PHPUnit 11 dropped --min-coverage; set XDEBUG_MODE=coverage and
        # parse the text report ourselves to enforce the threshold.
        env = os.environ.copy()
        php_extra = []
        if has_xdebug:
            env["XDEBUG_MODE"] = "coverage"
        else:
            php_extra = ["-d", "pcov.enabled=1"]

        cmd = ["php"] + php_extra + ["vendor/bin/phpunit", "--coverage-text"]
        res = self._run(cmd, cwd=path, timeout=120, env=env)

        output = (res.stdout or "") + (res.stderr or "")
        if res.returncode != 0:
            return self._fail(output)

        # Parse "Lines:  XX.XX% (N/M)" from the summary block
        m = re.search(r"Lines:\s+([\d.]+)%", output)
        if m:
            coverage = float(m.group(1))
            if coverage < _COVERAGE_THRESHOLD:
                return self._fail(
                    f"Coverage {coverage:.1f}% is below the required "
                    f"{_COVERAGE_THRESHOLD}%.\n\n{output[-2000:]}"
                )
        return self._ok()

    # ── Test discovery ────────────────────────────────────────────────────────

    def find_test_files(self, path: str) -> list[str]:
        """PHPUnit convention is *Test.php, not test_*.php."""
        return glob.glob(os.path.join(path, "tests", "**", "*Test.php"), recursive=True)

    # ── Example ───────────────────────────────────────────────────────────────

    def run_example(self, path: str) -> dict:
        ep = os.path.join(path, "examples", self.example_filename(path))
        res = self._run(["php", ep], cwd=path, timeout=60)
        if res.returncode != 0:
            return self._fail(res.stderr or res.stdout)
        return self._ok()

    # ── Required files ────────────────────────────────────────────────────────

    def required_files(self, path: str) -> list[tuple[str, str]]:
        return [
            (
                "composer.json",
                "composer.json is missing - required for dependency management. "
                "Recreate with: cartograph create",
            ),
            (
                "phpunit.xml",
                "phpunit.xml is missing - required for test runner configuration. "
                "Recreate with: cartograph create",
            ),
        ]

    def example_filename(self, path: str = "") -> str:
        return "example_usage.php"

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self, path: str) -> None:
        self._cleanup_artifact_dirs(path)

    # ── Validate override ─────────────────────────────────────────────────────

    def validate_widget(self, path: str, dependencies: list) -> dict:
        errors = []

        # PHP syntax check on all src/ files
        src_files = glob.glob(os.path.join(path, "src", "**", "*.php"), recursive=True)
        for fpath in src_files:
            res = self._run(["php", "-l", fpath], cwd=path, timeout=10)
            if res.returncode != 0:
                rel = os.path.relpath(fpath, path)
                errors.append(f"PHP syntax error in {rel}:\n{(res.stderr or res.stdout).strip()}")

        # Dependency version floor check
        errors.extend(self._check_dep_pinning(dependencies))

        if errors:
            return self._fail("\n".join(errors))
        return self._ok()
