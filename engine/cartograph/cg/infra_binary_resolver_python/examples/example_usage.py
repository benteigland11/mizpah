"""
Example: resolving an external binary with and without an override.

A CLI that shells out to `somebin` usually relies on PATH. Here we
demonstrate both the PATH lookup and the override path, using a
fabricated binary dropped into a temp directory so the example is
self-contained.
"""
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.binary_resolver import ResolveError, resolve


def main():
    with tempfile.TemporaryDirectory() as tmp:
        fake = os.path.join(tmp, "somebin")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)

        # 1. Explicit override - the caller knows exactly where the
        #    binary lives (from config, from auto-detection, etc).
        resolved = resolve("somebin", override=fake)
        print(f"override -> {resolved.path} (source={resolved.source})")

        # 2. No override - standard PATH lookup. This will fail for
        #    our fabricated binary since it isn't on PATH, but the
        #    shape of the error is what a CLI would surface to its
        #    user.
        try:
            resolve("somebin", override_key="paths.somebin")
        except ResolveError as e:
            print(f"path lookup -> ResolveError: {e}")


if __name__ == "__main__":
    main()
