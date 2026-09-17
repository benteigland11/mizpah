"""Terraform (HCL) language engine - infrastructure-as-code widgets.

Validation philosophy
---------------------
Terraform is declarative. There is no test runner that exercises behavior at
runtime; the only honest validation surface is shape: does the configuration
parse, do references resolve, does the module's contract (variables, outputs)
hold up when called from a root configuration?

So Cartograph validates Terraform widgets in two layers:

  - run_tests:   ``terraform validate`` on src/ AND on each tests/ root config.
                 src/ proves the module is internally consistent; tests/ proves
                 it is externally callable.
  - run_example: ``terraform validate`` on examples/ - the canonical consumer.

There is no coverage requirement. Widgets harden through use: each consumer
surfaces edge cases (apex DNS records, IAM perms, tagging conventions) that
feed back into the module. The cloud's proposal flow is the durability
mechanism, not unit tests.

Layout
------
    widget_root/
      src/        module: main.tf, variables.tf, outputs.tf, versions.tf
      tests/      root configs that call the module with sample inputs
      examples/   root config showing typical use
"""

import glob as _glob
import logging
import os
import re

from .base import LanguageEngine

log = logging.getLogger("cartograph")


# ---------------------------------------------------------------------------
# Comment-aware HCL helpers (small enough to inline; native scanner not needed)
# ---------------------------------------------------------------------------

def _strip_hcl_comments(content: str) -> str:
    """Replace HCL comments with spaces while preserving line/column positions.

    Recognizes // line, # line, and /* block */ comments. String literals are
    untouched, so contamination checks see the same line numbers as the
    original source.
    """
    out = []
    i = 0
    n = len(content)
    in_string = False
    while i < n:
        ch = content[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(content[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        # Line comments: //... or #...
        if ch == "#" or (ch == "/" and i + 1 < n and content[i + 1] == "/"):
            j = content.find("\n", i)
            if j == -1:
                j = n
            out.append(" " * (j - i))
            i = j
            continue
        # Block comment: /* ... */
        if ch == "/" and i + 1 < n and content[i + 1] == "*":
            j = content.find("*/", i + 2)
            if j == -1:
                j = n
            else:
                j += 2
            # Preserve newlines so line numbers line up
            for k in range(i, j):
                out.append("\n" if content[k] == "\n" else " ")
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Contamination patterns
# ---------------------------------------------------------------------------

# Credential assignments inside the module - access_key/secret_key/etc.
_CREDENTIAL_RE = re.compile(
    r'\b(?:access_key|secret_key|secret_access_key|password|api_key|auth_token|'
    r'client_secret|private_key)\s*=\s*"[^"]{6,}"',
    re.IGNORECASE,
)

# 12-digit AWS account IDs embedded in ARNs or assignments. Matches arn:aws:*
# strings carrying a real account id. Catches `arn:aws:iam::123456789012:role/x`.
_AWS_ACCOUNT_ID_RE = re.compile(
    r'arn:aws[\w-]*:[^:]*:[^:]*:(\d{12}):[^"\s]+',
)

# Public URLs that aren't placeholders. example.com / .test / localhost are fine.
_URL_RE = re.compile(
    r'"https?://(?!(?:localhost|127\.0\.0\.1|(?:[\w-]+\.)*example\.(?:com|org|net)|'
    r'[\w.-]+\.test(?:[/:"\'#?]|$)))[^"]+"',
    re.IGNORECASE,
)

# Absolute filesystem paths in string literals
_ABS_PATH_RE = re.compile(
    r'"(?:/home/|/Users/|/root/|/var/|/etc/|[A-Za-z]:[/\\\\])[^"]{3,}"',
)

# `provider "X" {` block in src/ - modules must NOT declare providers; they
# accept them implicitly or via configuration_aliases. Locking a provider in
# the module breaks consumer composability.
_PROVIDER_BLOCK_RE = re.compile(
    r'^[ \t]*provider\s+"[^"]+"\s*\{',
    re.MULTILINE,
)

# `terraform { backend "X" { ... } }` in src/ - backend choice is the
# consumer's, never the module's.
_BACKEND_BLOCK_RE = re.compile(
    r'\bbackend\s+"[^"]+"\s*\{',
)

# Hardcoded IPs (real, not 0.0.0.0 / 127.0.0.1 / private nonsense)
_IP_RE = re.compile(r'"(?:\d{1,3}\.){3}\d{1,3}(?:/\d+)?"')


# ---------------------------------------------------------------------------
# Scaffold templates
# ---------------------------------------------------------------------------

_TF_MAIN = """\
# {name}
# Terraform module - shape is validated by `terraform validate`.
# Iterates well across consumers; harden through use.

resource "null_resource" "{module}" {{
  # [TODO] Replace with the resource(s) this module manages.
  # null_resource keeps the scaffold validating before you wire real providers.
  triggers = {{
    name = var.name
  }}
}}
"""

_TF_VARIABLES = """\
variable "name" {{
  description = "Logical name for the {module} resource."
  type        = string
}}

variable "tags" {{
  description = "Tags applied to all managed resources."
  type        = map(string)
  default     = {{}}
}}
"""

_TF_OUTPUTS = """\
output "id" {{
  description = "Identifier of the managed resource."
  value       = null_resource.{module}.id
}}
"""

_TF_VERSIONS = """\
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    null = {
      source  = "hashicorp/null"
      version = ">= 3.0.0"
    }
  }
}
"""

_TF_TEST = """\
# Sample root configuration that calls the module with minimal inputs.
# `terraform validate` here proves the module is externally callable -
# inputs/outputs match, and a consumer can wire it up without surprises.

terraform {{
  required_version = ">= 1.5.0"
  required_providers {{
    null = {{
      source  = "hashicorp/null"
      version = ">= 3.0.0"
    }}
  }}
}}

module "{module}" {{
  source = "../src"
  name   = "test-{module}"
  tags   = {{
    environment = "test"
  }}
}}
"""

_TF_EXAMPLE = """\
# Example consumer of {name}. Demonstrates a realistic call pattern.
# Validated with `terraform validate`.

terraform {{
  required_version = ">= 1.5.0"
  required_providers {{
    null = {{
      source  = "hashicorp/null"
      version = ">= 3.0.0"
    }}
  }}
}}

module "{module}" {{
  source = "../src"
  name   = "example-{module}"
  tags   = {{
    environment = "demo"
    managed_by  = "terraform"
  }}
}}

output "{module}_id" {{
  value = module.{module}.id
}}
"""


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class TerraformEngine(LanguageEngine):
    name = "terraform"
    file_ext = "tf"
    aliases = ["tf"]
    toolchain = {"terraform": "Install Terraform - terraform.io/downloads"}
    validation_version = 1
    supported = True

    # No native scanner - HCL contamination is small enough for Python regex
    # with a comment-stripping pre-pass.
    scanner_runner = []

    # ---- toolchain --------------------------------------------------------

    def runtime_version(self) -> str | None:
        try:
            res = self._run(["terraform", "-version"], cwd=os.getcwd(), timeout=10)
        except Exception:
            return None
        if not res or res.returncode != 0:
            return None
        first = (res.stdout or "").splitlines()[0:1]
        return first[0].strip() if first else None

    # ---- scaffold ---------------------------------------------------------

    def scaffold(self, target_dir, module_name, display_name, **kwargs):
        src_dir = os.path.join(target_dir, "src")
        tests_dir = os.path.join(target_dir, "tests")
        ex_dir = os.path.join(target_dir, "examples")
        os.makedirs(src_dir, exist_ok=True)
        os.makedirs(tests_dir, exist_ok=True)
        os.makedirs(ex_dir, exist_ok=True)

        with open(os.path.join(src_dir, f"{module_name}.tf"), "w", encoding="utf-8") as f:
            f.write(_TF_MAIN.format(module=module_name, name=display_name))
        with open(os.path.join(src_dir, "variables.tf"), "w", encoding="utf-8") as f:
            f.write(_TF_VARIABLES.format(module=module_name))
        with open(os.path.join(src_dir, "outputs.tf"), "w", encoding="utf-8") as f:
            f.write(_TF_OUTPUTS.format(module=module_name))
        with open(os.path.join(src_dir, "versions.tf"), "w", encoding="utf-8") as f:
            f.write(_TF_VERSIONS)
        with open(os.path.join(tests_dir, f"test_{module_name}.tf"), "w", encoding="utf-8") as f:
            f.write(_TF_TEST.format(module=module_name))
        with open(os.path.join(ex_dir, "example_usage.tf"), "w", encoding="utf-8") as f:
            f.write(_TF_EXAMPLE.format(module=module_name, name=display_name))

    def find_test_files(self, path: str) -> list[str]:
        return _glob.glob(os.path.join(path, "tests", "*.tf"))

    def example_filename(self, path: str = "") -> str:
        return "example_usage.tf"

    def required_files(self, path: str) -> list[tuple[str, str]]:
        # versions.tf in src/ is the only hard requirement - every Terraform
        # module should declare its terraform/provider version constraints so
        # consumers know what they're getting.
        versions = os.path.join(path, "src", "versions.tf")
        if not os.path.isfile(versions):
            return [("src/versions.tf",
                     "Terraform modules must declare required_version and "
                     "required_providers in src/versions.tf")]
        return []

    # ---- dependencies (terraform init) ------------------------------------

    def install_deps(self, path: str, dependencies: list) -> None:
        """Run terraform init in src/, tests/, examples/ to fetch providers.

        Sets TF_PLUGIN_CACHE_DIR so providers are downloaded once across
        directories - first init is slow, subsequent inits hit the cache.
        Cartograph does not enforce widget.json dependencies for terraform;
        provider versions are pinned in the widget's own versions.tf and
        propagate via the standard required_providers mechanism.
        """
        env = os.environ.copy()
        cache_dir = env.get("TF_PLUGIN_CACHE_DIR") or os.path.expanduser(
            "~/.terraform.d/plugin-cache")
        os.makedirs(cache_dir, exist_ok=True)
        env["TF_PLUGIN_CACHE_DIR"] = cache_dir
        # -input=false: never prompt; -backend=false: skip backend init since
        # validate-only doesn't need state.
        for sub in ("src", "tests", "examples"):
            sub_path = os.path.join(path, sub)
            if not os.path.isdir(sub_path):
                continue
            if not _glob.glob(os.path.join(sub_path, "*.tf")):
                continue
            res = self._run(
                ["terraform", "init", "-input=false", "-backend=false",
                 "-no-color"],
                cwd=sub_path, timeout=180, env=env,
            )
            if res.returncode != 0:
                output = (res.stderr or res.stdout or "").strip()
                raise RuntimeError(
                    f"terraform init failed in {sub}/:\n{output[:2000]}"
                )

    # ---- validation -------------------------------------------------------

    def _tf_validate(self, dirpath: str) -> tuple[bool, str]:
        res = self._run(
            ["terraform", "validate", "-no-color"],
            cwd=dirpath, timeout=60,
        )
        if res.returncode == 0:
            return True, ""
        return False, (res.stderr or res.stdout or "").strip()

    def run_tests(self, path: str) -> dict:
        """Validate src/ (module shape) AND each tests/*.tf (callability)."""
        src_dir = os.path.join(path, "src")
        if not _glob.glob(os.path.join(src_dir, "*.tf")):
            return self._fail("No .tf files found in src/")

        ok, err = self._tf_validate(src_dir)
        if not ok:
            return self._fail(f"src/ validation failed:\n{err}")

        tests_dir = os.path.join(path, "tests")
        test_files = _glob.glob(os.path.join(tests_dir, "*.tf"))
        if not test_files:
            return self._fail(
                "No .tf files in tests/ - add at least one root configuration "
                "that calls the module so external callability is verified"
            )
        ok, err = self._tf_validate(tests_dir)
        if not ok:
            return self._fail(f"tests/ validation failed:\n{err}")
        return self._ok()

    def run_example(self, path: str) -> dict:
        ex_dir = os.path.join(path, "examples")
        if not _glob.glob(os.path.join(ex_dir, "*.tf")):
            return self._fail("No .tf files found in examples/")
        ok, err = self._tf_validate(ex_dir)
        if not ok:
            return self._fail(f"examples/ validation failed:\n{err}")
        return self._ok()

    # ---- contamination ----------------------------------------------------

    def scan_contamination(self, path: str, tech_stack: dict) -> dict:
        """HCL contamination scan with comment stripping.

        Blocks:
          - credential-shaped assignments (access_key, password, etc.)
          - real AWS account IDs in arn:aws:* strings
          - absolute filesystem paths
          - `provider "X" {` blocks in src/
          - `backend "X" {` blocks in src/
          - hardcoded public IPs in src/

        Warnings:
          - hardcoded URLs (real domains)
          - hardcoded IPs in tests/examples
        """
        blocks: list[str] = []
        warnings: list[str] = []

        all_files = []
        for sub, kind in (("src", "src"), ("tests", "test"),
                          ("examples", "example")):
            for f in _glob.glob(os.path.join(path, sub, "**", "*.tf"),
                                recursive=True):
                all_files.append((f, kind))

        for filepath, kind in all_files:
            rel = os.path.relpath(filepath, path)
            try:
                raw = open(filepath, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            content = _strip_hcl_comments(raw)

            for m in _CREDENTIAL_RE.finditer(content):
                line_no = content[:m.start()].count("\n") + 1
                msg = (f"Possible credential in {rel}:{line_no}: "
                       f"{m.group()[:120]}")
                if kind == "src":
                    blocks.append(msg)
                else:
                    warnings.append(msg + " - verify it's fake test data")

            for m in _AWS_ACCOUNT_ID_RE.finditer(content):
                line_no = content[:m.start()].count("\n") + 1
                acct = m.group(1)
                if acct in ("000000000000", "123456789012"):
                    continue  # documented placeholders
                blocks.append(
                    f"Real AWS account ID {acct} in {rel}:{line_no} - "
                    f"replace with a placeholder or accept it as a variable"
                )

            for m in _ABS_PATH_RE.finditer(content):
                line_no = content[:m.start()].count("\n") + 1
                blocks.append(f"Absolute path in {rel}:{line_no}: "
                              f"{m.group()[:120]}")

            if kind == "src":
                for m in _PROVIDER_BLOCK_RE.finditer(content):
                    line_no = content[:m.start()].count("\n") + 1
                    blocks.append(
                        f"{rel}:{line_no}: `provider` block in src/ - "
                        f"modules must not declare providers; let consumers "
                        f"configure them"
                    )
                for m in _BACKEND_BLOCK_RE.finditer(content):
                    line_no = content[:m.start()].count("\n") + 1
                    blocks.append(
                        f"{rel}:{line_no}: `backend` block in src/ - "
                        f"backend choice belongs to the consumer's root config"
                    )

            for m in _URL_RE.finditer(content):
                line_no = content[:m.start()].count("\n") + 1
                msg = f"Hardcoded URL in {rel}:{line_no}: {m.group()[:120]}"
                if kind == "src":
                    warnings.append(msg + " - accept as a variable")
                else:
                    warnings.append(msg)

            for m in _IP_RE.finditer(content):
                ip = m.group().strip('"')
                # Skip obvious wildcards / loopbacks / RFC 1918 placeholders
                if ip.split("/")[0] in ("0.0.0.0", "127.0.0.1", "255.255.255.255"):
                    continue
                line_no = content[:m.start()].count("\n") + 1
                msg = f"Hardcoded IP in {rel}:{line_no}: {ip}"
                if kind == "src":
                    blocks.append(msg + " - accept as a variable")
                else:
                    warnings.append(msg + " - verify it's not project-specific")

        return {"blocks": blocks, "warnings": warnings}

    # ---- cleanup ----------------------------------------------------------

    def cleanup(self, path: str) -> None:
        """Remove .terraform/ dirs and lockfiles produced by terraform init."""
        import shutil
        for sub in ("src", "tests", "examples"):
            tdir = os.path.join(path, sub, ".terraform")
            if os.path.isdir(tdir):
                shutil.rmtree(tdir, ignore_errors=True)
            lockfile = os.path.join(path, sub, ".terraform.lock.hcl")
            if os.path.isfile(lockfile):
                try:
                    os.remove(lockfile)
                except OSError:
                    pass
        self._cleanup_artifact_dirs(path)
