"""Dependency-environment cache.

Lets a language engine skip re-installing project-local dependencies
(``.venv``, ``node_modules``, ``vendor``) when the declared dependency set
and the lockfiles are unchanged since the last successful install.

Built on the ``infra_file_stamp_python`` widget - the same fingerprint
primitive that backs ``validation_stamp`` - but keyed on its own stamp file
so the dep-cache and validation-stamp lifecycles never collide.

The cache key is (language, sha256 of the declared dependency set) plus a
file fingerprint over any lockfiles the engine declares. A change to either
the declared deps or a lockfile invalidates the cache and forces a fresh
install, so reuse never validates against stale dependencies.
"""

import hashlib
import json
import logging
import os

# parse_requirement / satisfies are provided by the version-constraint widget;
# re-exported here so engines have a single dep-cache import surface.
from cg.infra_version_constraint_satisfies_python.src.version_constraint_satisfies import (  # noqa: F401
    parse_requirement,
    satisfies,
)

log = logging.getLogger("cartograph")

DEP_STAMP_FILE = ".dep_cache.json"


def _deps_hash(dependencies) -> str:
    """Stable hash of the declared dependency set, order-independent."""
    blob = json.dumps(sorted(str(d) for d in (dependencies or [])))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def deps_satisfied(path, language, dependencies, env_dir, lock_patterns) -> bool:
    """Return True if a usable install already exists for this exact dep set.

    Requires all of: the env dir is present on disk, a dep stamp exists whose
    language and dependency hash match, and no lockfile in ``lock_patterns``
    changed since the stamp was written.
    """
    if not env_dir or not os.path.isdir(os.path.join(path, env_dir)):
        return False
    try:
        from cg.infra_file_stamp_python.src.file_stamp import is_stamp_valid
    except ImportError:
        return False
    ok = is_stamp_valid(
        path,
        metadata_match={"language": language, "deps": _deps_hash(dependencies)},
        stamp_name=DEP_STAMP_FILE,
        patterns=lock_patterns,
    )
    if ok:
        log.debug("Dependency cache hit for %s (%s) - skipping install", path, language)
    return ok


def record_deps(path, language, dependencies, lock_patterns) -> None:
    """Write the dep stamp after a successful install. Best-effort."""
    try:
        from cg.infra_file_stamp_python.src.file_stamp import write_stamp
    except ImportError:
        return
    write_stamp(
        path,
        metadata={"language": language, "deps": _deps_hash(dependencies)},
        stamp_name=DEP_STAMP_FILE,
        patterns=lock_patterns,
    )
    log.debug("Dependency cache stamp written for %s (%s)", path, language)
