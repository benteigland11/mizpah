"""Demonstrate the three locator forms with synthetic facts (no disk I/O)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.resource_ref_resolve import ResolveFacts, resolve_module_ref

# 1) Directory path form (what broke cloud publish before)
r1 = resolve_module_ref(
    "cg/example_widget_python",
    path=".",
    facts=ResolveFacts(
        token_dir="proj/cg/example_widget_python",
        token_manifest_id="example-widget-python",
    ),
)
print("dir form:", r1.id, r1.via, r1.path)

# 2) --lib id form
r2 = resolve_module_ref(
    "example-widget-python",
    lib=True,
    facts=ResolveFacts(
        lib_path="lib/example-widget-python",
        token_manifest_id="example-widget-python",
    ),
)
print("lib form:", r2.id, r2.via)

# 3) Bare id → library
r3 = resolve_module_ref(
    "example-widget-python",
    facts=ResolveFacts(
        lib_path="lib/example-widget-python",
        token_manifest_id="example-widget-python",
    ),
)
print("id form:", r3.id, r3.via)

# Cloud ref
r4 = resolve_module_ref("@alice/example-widget-python")
print("cloud:", r4.kind, r4.id)
