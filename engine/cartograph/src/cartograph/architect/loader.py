"""Load an Architecture instance from architect.py."""

from cg.cg_universal_python_module_loader_python.src.python_module_loader import (
    InstanceNotFoundError,
    InstanceTypeMismatchError,
    ModuleFileNotFoundError,
    ModuleLoadError,
    ModuleSyntaxError,
    MultipleInstancesError,
    load_instance,
)

from .schema import Architecture


class ArchitectLoadError(Exception):
    """Wraps loader failures with architect-specific messaging."""


def load_architecture(path: str) -> Architecture:
    """Load architect.py and return its Architecture instance.

    The user's architect.py must define exactly one Architecture()
    instance at module level (any binding name).
    """
    try:
        return load_instance(path, Architecture)
    except ModuleFileNotFoundError as e:
        raise ArchitectLoadError(
            f"No architect.py found at {path}. "
            f"Run `cartograph architect init` to scaffold one."
        ) from e
    except ModuleSyntaxError as e:
        raise ArchitectLoadError(f"architect.py failed to load: {e}") from e
    except InstanceNotFoundError as e:
        raise ArchitectLoadError(
            f"architect.py defines no Architecture() instance. "
            f"Add one at module level: `architecture = Architecture(...)`."
        ) from e
    except InstanceTypeMismatchError as e:
        raise ArchitectLoadError(str(e)) from e
    except MultipleInstancesError as e:
        raise ArchitectLoadError(
            f"architect.py defines more than one Architecture() instance. "
            f"Keep exactly one."
        ) from e
    except ModuleLoadError as e:
        raise ArchitectLoadError(str(e)) from e
