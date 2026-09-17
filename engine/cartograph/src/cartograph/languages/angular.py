"""Angular language engine.

Angular is a standalone engine (not a TypeScript subclass) because:
  - Test runner: ng test --watch=false (Karma, not vitest)
  - Example validation: ng build (build artifact, not script execution)
  - Install: npm install + requires @angular/cli globally

Source scanning reuses js_scanner.js since Angular uses TypeScript syntax.
"""

import glob as _glob
import json
import os
import re

from .base import LanguageEngine, _dep_bare_name, log

_COVERAGE_THRESHOLD = 80


def _to_class_name(module_name: str) -> str:
    """Convert hyphen/underscore-separated module name to PascalCase."""
    return "".join(part.capitalize() for part in re.split(r"[-_]", module_name))


# -- Scaffold templates --------------------------------------------------------
# Use __PLACEHOLDER__ substitution instead of .format() to avoid escaping
# the many braces in TypeScript and JSON template content.

_KARMA_CONF_JS = """\
module.exports = function (config) {
  config.set({
    basePath: '',
    frameworks: ['jasmine', '@angular-devkit/build-angular'],
    plugins: [
      require('karma-jasmine'),
      require('karma-chrome-launcher'),
      require('karma-jasmine-html-reporter'),
      require('karma-coverage'),
      require('@angular-devkit/build-angular/plugins/karma'),
    ],
    client: {
      jasmine: { random: false },
    },
    reporters: ['progress', 'kjhtml', 'coverage'],
    coverageReporter: {
      dir: require('path').join(__dirname, './coverage'),
      subdir: '.',
      reporters: [{ type: 'lcov' }, { type: 'text-summary' }],
      check: {
        global: {
          statements: __THRESHOLD__,
          branches: __THRESHOLD__,
          functions: __THRESHOLD__,
          lines: __THRESHOLD__,
        },
      },
    },
    port: 9876,
    colors: true,
    logLevel: config.LOG_INFO,
    autoWatch: false,
    browsers: ['ChromeHeadlessNoSandbox'],
    customLaunchers: {
      ChromeHeadlessNoSandbox: {
        base: 'ChromeHeadless',
        flags: ['--no-sandbox', '--disable-gpu'],
      },
    },
    singleRun: true,
  });
};
"""

_TEST_BOOTSTRAP_TS = """\
// This file bootstraps the Angular testing environment for Karma.
// Required for TestBed.configureTestingModule() to work in tests.
import 'zone.js';
import 'zone.js/testing';
import { getTestBed } from '@angular/core/testing';
import {
  BrowserDynamicTestingModule,
  platformBrowserDynamicTesting,
} from '@angular/platform-browser-dynamic/testing';

getTestBed().initTestEnvironment(
  BrowserDynamicTestingModule,
  platformBrowserDynamicTesting(),
);
"""

_PUBLIC_API_TS = """\
export * from './__MODULE__.component';
"""

_COMPONENT_TS = """\
import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * __DISPLAY__ component.
 *
 * No project-specific names, identifiers, or terminology in src/.
 * Use generic names: item, value, result, input, output.
 */
@Component({
  selector: 'lib-__SELECTOR__',
  standalone: true,
  imports: [CommonModule],
  template: `<div class="lib-container">{{ message }}</div>`,
  styles: [`
    .lib-container {
      padding: 1rem;
    }
  `],
})
export class __CLASS__Component {
  message = 'Hello from __DISPLAY__';

  // TODO: Replace with your component logic
  greet(name: string): string {
    return `Hello, ${name}!`;
  }
}
"""

_TEST_TS = """\
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { __CLASS__Component } from '../src/__MODULE__.component';

describe('__CLASS__Component', () => {
  let component: __CLASS__Component;
  let fixture: ComponentFixture<__CLASS__Component>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [__CLASS__Component],
    }).compileComponents();
    fixture = TestBed.createComponent(__CLASS__Component);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should greet', () => {
    // TODO: Replace with meaningful tests for your component
    expect(component.greet('World')).toBe('Hello, World!');
  });
});
"""

_EXAMPLE_TS = """\
/**
 * Example usage of __DISPLAY__.
 *
 * Angular components cannot be executed directly as scripts.
 * This file demonstrates how to integrate __CLASS__Component into
 * an Angular application.
 *
 * Validation: this file is included in tsconfig.lib.json and must
 * compile without TypeScript errors. ng build will fail if the
 * import path is wrong or types are incorrect.
 *
 * No network calls, external services, or API keys in examples.
 */
import { __CLASS__Component } from '../src/__MODULE__.component';

// ----- Integration example -----

// Add to a standalone component:
//   @Component({
//     imports: [__CLASS__Component],
//     template: '<lib-__SELECTOR__></lib-__SELECTOR__>'
//   })
//   export class AppComponent {}

// ----- Programmatic API -----
// (Inject via Angular DI in a real app; instantiated here for type-checking only.)
const _component = new __CLASS__Component();
console.log(_component.greet('World')); // Hello, World!

export { __CLASS__Component };
"""


def _sub(template: str, module: str = "", class_name: str = "", display: str = "",
         project: str = "", threshold: int = 0, selector: str = "") -> str:
    """Substitute __PLACEHOLDER__ markers in a template string."""
    s = template
    if module:
        s = s.replace("__MODULE__", module)
    if class_name:
        s = s.replace("__CLASS__", class_name)
    if display:
        s = s.replace("__DISPLAY__", display)
    if project:
        s = s.replace("__PROJECT__", project)
    if threshold:
        s = s.replace("__THRESHOLD__", str(threshold))
    if selector:
        s = s.replace("__SELECTOR__", selector)
    return s


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class AngularEngine(LanguageEngine):
    name = "angular"
    aliases = ["ang", "ng"]
    validation_version = 2
    file_ext = "ts"
    supported = True
    toolchain = {
        "ng": "Install Angular CLI globally - npm install -g @angular/cli",
        "node": "Install Node.js 18+ - nodejs.org",
        "npm": "Reinstall Node.js - npm ships with it",
    }
    scanner_runner = ["node"]
    scanner_messages = {
        "console_log": "console.log() found in src/ - remove debug output before checkin:",
        "process_exit": "process.exit() found in src/ - widgets must not exit the process:",
        "eval": "eval() found in src/ - dynamic code execution is a security risk:",
    }
    scanner_warning_messages = {
        "abs_path": "Absolute paths found in src/ - widgets must be portable:",
        "credential": "Possible credentials found in src/ - remove before checkin:",
        "hardcoded_url": "Hardcoded URLs found in src/ - consider making these configurable:",
        "hardcoded_ip": "Hardcoded IPs found in src/ - consider making these configurable:",
        "hardcoded_value": "Hardcoded values found in src/ - consider making these configurable:",
        "env_var": "Environment variable access found in src/ - verify it's not project-specific:",
        "unlisted_import": "Unlisted imports - add to widget.json dependencies or remove:",
        "sleep": "Sleep/blocking calls found - widgets must not block the caller:",
        "risky_import": "Node.js I/O or network imports found in src/ - ensure no hardcoded paths, URLs, or commands:",
    }
    manifest_patterns = ["package.json", "angular.json"]
    env_dir = "node_modules"

    def lock_patterns(self, path: str) -> list:
        pats = []
        for f in ("package-lock.json", "package.json"):
            fp = os.path.join(path, f)
            if os.path.exists(fp):
                pats.append(fp)
        return pats

    def verify_installed(self, path: str, dependencies: list) -> bool:
        # node_modules verification is identical to plain JS.
        from .javascript import JavaScriptEngine
        return JavaScriptEngine.verify_installed(self, path, dependencies)

    # -- Runtime -----------------------------------------------------------

    def runtime_version(self) -> str | None:
        try:
            res = self._run(["ng", "version", "--no-interactive"], cwd=".", timeout=15)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("Angular CLI:"):
                        ver = stripped.split(":", 1)[1].strip()
                        return f"ng {ver}"
        except Exception:
            pass
        return None

    # -- Scaffold ----------------------------------------------------------

    def scaffold(self, target_dir, module_name, display_name, **kwargs):
        project_name = os.path.basename(target_dir)
        class_name = _to_class_name(module_name)
        selector = module_name.replace("_", "-")  # kebab-case for Angular selector

        # -- JSON config files (built as dicts to avoid escaping braces) --
        package_json = {
            "name": project_name,
            "version": "1.0.0",
            "scripts": {"test": "ng test --watch=false"},
            "dependencies": {
                "tslib": "^2.3.0",
            },
            "peerDependencies": {
                "@angular/common": "^17.0.0",
                "@angular/core": "^17.0.0",
                "rxjs": "~7.8.0",
            },
            "devDependencies": {
                "@angular-devkit/build-angular": "^17.0.0",
                "@angular/cli": "^17.0.0",
                "@angular/compiler": "^17.0.0",
                "@angular/compiler-cli": "^17.0.0",
                "@angular/platform-browser": "^17.0.0",
                "@angular/platform-browser-dynamic": "^17.0.0",
                "@types/jasmine": "~5.1.0",
                "jasmine-core": "~5.1.0",
                "karma": "~6.4.0",
                "karma-chrome-launcher": "~3.2.0",
                "karma-coverage": "~2.2.0",
                "karma-jasmine": "~5.1.0",
                "karma-jasmine-html-reporter": "~2.1.0",
                "ng-packagr": "^17.0.0",
                "typescript": "~5.2.0",
                "zone.js": "~0.14.0",
            },
        }

        angular_json = {
            "$schema": "./node_modules/@angular/cli/lib/config/schema.json",
            "version": 1,
            "newProjectRoot": "projects",
            "projects": {
                project_name: {
                    "projectType": "library",
                    "root": "",
                    "sourceRoot": "src",
                    "prefix": "lib",
                    "architect": {
                        "build": {
                            "builder": "@angular-devkit/build-angular:ng-packagr",
                            "options": {
                                "project": "ng-package.json",
                                "tsConfig": "tsconfig.lib.json",
                            },
                            "configurations": {
                                "production": {"tsConfig": "tsconfig.lib.json"}
                            },
                        },
                        "test": {
                            "builder": "@angular-devkit/build-angular:karma",
                            "options": {
                                "main": "src/test.ts",
                                "tsConfig": "tsconfig.spec.json",
                                "karmaConfig": "karma.conf.js",
                                "include": ["../tests/**/*.ts"],
                                "codeCoverage": True,
                            },
                        },
                    },
                }
            },
        }

        tsconfig_json = {
            "compileOnSave": False,
            "compilerOptions": {
                "strict": True,
                "noImplicitOverride": True,
                "noPropertyAccessFromIndexSignature": True,
                "noImplicitReturns": True,
                "noFallthroughCasesInSwitch": True,
                "sourceMap": True,
                "declaration": False,
                "experimentalDecorators": True,
                "moduleResolution": "bundler",
                "importHelpers": True,
                "target": "ES2022",
                "module": "ES2022",
                "useDefineForClassFields": False,
                "lib": ["ES2022", "dom"],
                "skipLibCheck": True,
            },
            "angularCompilerOptions": {
                "enableI18nLegacyMessageIdFormat": False,
                "strictInjectionParameters": True,
                "strictInputAccessModifiers": True,
                "strictTemplates": True,
            },
        }

        tsconfig_lib_json = {
            "extends": "./tsconfig.json",
            "compilerOptions": {
                "outDir": "../../out-tsc/lib",
                "declaration": True,
                "declarationMap": True,
                "inlineSources": True,
                "types": [],
            },
            "include": ["src/**/*.ts", "examples/**/*.ts"],
            "exclude": ["tests/**/*.ts", "**/*.spec.ts"],
        }

        tsconfig_spec_json = {
            "extends": "./tsconfig.json",
            "compilerOptions": {
                "outDir": "../../out-tsc/spec",
                "types": ["jasmine"],
            },
            "include": ["src/test.ts", "tests/**/*.ts"],
        }

        ng_package_json = {
            "$schema": "./node_modules/ng-packagr/ng-package.schema.json",
            "lib": {"entryFile": "src/public-api.ts"},
        }

        # Write JSON config files
        for filename, obj in [
            ("package.json", package_json),
            ("angular.json", angular_json),
            ("tsconfig.json", tsconfig_json),
            ("tsconfig.lib.json", tsconfig_lib_json),
            ("tsconfig.spec.json", tsconfig_spec_json),
            ("ng-package.json", ng_package_json),
        ]:
            with open(os.path.join(target_dir, filename), "w", encoding="utf-8") as f:
                json.dump(obj, f, indent=2)
                f.write("\n")

        # Write karma.conf.js
        _write(
            os.path.join(target_dir, "karma.conf.js"),
            _sub(_KARMA_CONF_JS, module="", threshold=_COVERAGE_THRESHOLD),
        )

        # Write TypeScript source files
        _write(
            os.path.join(target_dir, "src", "test.ts"),
            _TEST_BOOTSTRAP_TS,
        )
        _write(
            os.path.join(target_dir, "src", "public-api.ts"),
            _sub(_PUBLIC_API_TS, module=module_name),
        )
        _write(
            os.path.join(target_dir, "src", f"{module_name}.component.ts"),
            _sub(_COMPONENT_TS, module=module_name, class_name=class_name,
                 display=display_name, selector=selector),
        )
        _write(
            os.path.join(target_dir, "tests", f"test_{module_name}.component.ts"),
            _sub(_TEST_TS, module=module_name, class_name=class_name),
        )
        _write(
            os.path.join(target_dir, "examples", "example_usage.ts"),
            _sub(_EXAMPLE_TS, module=module_name, class_name=class_name,
                 display=display_name, selector=selector),
        )

    # -- Validation --------------------------------------------------------

    def validate_widget(self, path: str, dependencies: list) -> dict:
        """Scan src/ using js_scanner.js (Angular uses TypeScript syntax)."""
        errors = []
        src_dir = os.path.join(path, "src")
        src_files = _glob.glob(os.path.join(src_dir, "**", "*.ts"), recursive=True)
        scanner = os.path.join(os.path.dirname(__file__), "scanners", "js_scanner.js")
        scan_errors, _, _ = self._run_native_scanner(
            scanner_path=scanner,
            runner=self.scanner_runner,
            src_files=src_files,
            cwd=path,
            finding_messages=self.scanner_messages,
        )
        errors.extend(scan_errors)
        errors.extend(self._check_dep_pinning(dependencies))
        if errors:
            return self._fail("\n".join(errors))
        return self._ok()

    def scan_contamination(self, path: str, widget: dict) -> dict:
        """Angular contamination: js_scanner.js on src + tests + examples."""
        src_files = _glob.glob(os.path.join(path, "src", "**", "*.ts"), recursive=True)
        test_files = _glob.glob(os.path.join(path, "tests", "**", "*.ts"), recursive=True)
        example_files = _glob.glob(
            os.path.join(path, "examples", "**", "*.ts"), recursive=True
        )
        all_files = src_files + test_files + example_files
        scanner = os.path.join(os.path.dirname(__file__), "scanners", "js_scanner.js")
        scan_errors, scan_warnings, scan_blocks = self._run_native_scanner(
            scanner_path=scanner,
            runner=self.scanner_runner,
            src_files=all_files,
            cwd=path,
            finding_messages=self.scanner_messages,
        )
        return {"blocks": scan_blocks + scan_errors, "warnings": scan_warnings}

    # -- Install -----------------------------------------------------------

    def install_deps(self, path: str, dependencies: list) -> None:
        if self._deps_cached(path, dependencies):
            log.debug("Reusing cached node_modules at %s", path)
            return
        package_json_path = os.path.join(path, "package.json")
        if os.path.exists(package_json_path) and dependencies:
            with open(package_json_path, encoding="utf-8") as f:
                pkg = json.load(f)
            changed = False
            for dep in dependencies:
                bare = _dep_bare_name(dep)
                ver_part = dep[len(bare):].strip() or "*"
                already_present = (
                    bare in pkg.get("dependencies", {})
                    or bare in pkg.get("peerDependencies", {})
                    or bare in pkg.get("devDependencies", {})
                )
                if bare and not already_present:
                    pkg.setdefault("dependencies", {})[bare] = ver_part
                    changed = True
            if changed:
                with open(package_json_path, "w", encoding="utf-8") as f:
                    json.dump(pkg, f, indent=2)
                    f.write("\n")

        # Shared npm cache (see javascript.py:_shared_npm_cache for rationale).
        from .javascript import _shared_npm_cache
        npm_cache = _shared_npm_cache()

        lockfile = os.path.join(path, "package-lock.json")
        use_ci = os.path.exists(lockfile)

        if use_ci:
            res = self._run(
                ["npm", "ci", "--cache", npm_cache], cwd=path, timeout=600,
            )
            if res.returncode != 0:
                log.debug("npm ci failed, regenerating lockfile via npm install")
                use_ci = False
        if not use_ci:
            res = self._run(
                ["npm", "install", "--cache", npm_cache], cwd=path, timeout=600,
            )

        if res.returncode != 0:
            output = (res.stderr or res.stdout or "").strip()
            raise RuntimeError(
                "Failed to install npm dependencies."
                + (f"\n{output[:2000]}" if output else "")
            )

        self._record_dep_cache(path, dependencies)

    # -- Tests -------------------------------------------------------------

    def run_tests(self, path: str) -> dict:
        res = self._run(
            ["ng", "test", "--watch=false", "--no-progress"],
            cwd=path,
            timeout=300,
        )
        if res.returncode != 0:
            return self._fail(res.stderr or res.stdout)
        return self._ok()

    # -- Example -----------------------------------------------------------

    def run_example(self, path: str) -> dict:
        """Build the Angular library to validate src/ and examples/ compile cleanly.

        Angular components cannot be executed as scripts. Example validation
        uses ng build (build artifact pattern, same as OpenSCAD's render check).
        examples/example_usage.ts is included in tsconfig.lib.json so type
        errors there also fail the build.
        """
        project_name = self._get_project_name(path)
        if not project_name:
            return self._fail(
                "angular.json not found or has no projects defined. "
                "Recreate the widget with: cartograph create"
            )
        res = self._run(
            ["ng", "build", project_name, "--configuration", "production"],
            cwd=path,
            timeout=300,
        )
        if res.returncode != 0:
            return self._fail(res.stderr or res.stdout)
        return self._ok()

    # -- Interface ---------------------------------------------------------

    def required_files(self, path: str) -> list[tuple[str, str]]:
        return [
            (
                "angular.json",
                "angular.json is missing - required for ng test and ng build. "
                "Recreate with: cartograph create",
            ),
            (
                "package.json",
                "package.json is missing - required for npm install. "
                "Recreate with: cartograph create",
            ),
            (
                "karma.conf.js",
                "karma.conf.js is missing - required for ng test with coverage. "
                "Recreate with: cartograph create",
            ),
            (
                "ng-package.json",
                "ng-package.json is missing - required for ng build. "
                "Recreate with: cartograph create",
            ),
            (
                "src/test.ts",
                "src/test.ts is missing - required to bootstrap TestBed for Karma. "
                "Recreate with: cartograph create",
            ),
        ]

    def example_filename(self, path: str = "") -> str:
        return "example_usage.ts"

    def find_test_files(self, path: str) -> list[str]:
        return _glob.glob(
            os.path.join(path, "tests", "**", "test_*.ts"), recursive=True
        )

    # -- Cleanup -----------------------------------------------------------

    def cleanup(self, path: str) -> None:
        self._cleanup_artifact_dirs(path)

    # -- Helpers -----------------------------------------------------------

    def _get_project_name(self, path: str) -> str | None:
        """Read the first project name from angular.json."""
        angular_json = os.path.join(path, "angular.json")
        try:
            with open(angular_json, encoding="utf-8") as f:
                config = json.load(f)
            return next(iter(config.get("projects", {})), None)
        except Exception:
            return None
