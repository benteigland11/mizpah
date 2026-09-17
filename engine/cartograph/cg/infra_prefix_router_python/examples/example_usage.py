"""Example: route namespaced ids to registry-tier handlers.

Demonstrates the typical use case - dispatching ids like 'cg-bp-foo' or
'example_org-bar' to per-tier config without an if/elif ladder. Handlers
are arbitrary opaque values; here we use config dicts.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.prefix_router import PrefixRouter


def main() -> None:
    router = PrefixRouter(default={"tier": "local"})
    router.register("cg-bp-", {"tier": "public-blueprints"})
    router.register("cg-", {"tier": "public-widgets"})
    router.register("example_org-", {"tier": "private", "org": "example_org"})

    sample_ids = [
        "cg-bp-auth-flow-python",
        "cg-backend-retry-python",
        "example_org-internal-tool-python",
        "data-local-only-python",
    ]
    for wid in sample_ids:
        config = router.resolve(wid)
        print(f"{wid:<40} -> {config}")


if __name__ == "__main__":
    main()
