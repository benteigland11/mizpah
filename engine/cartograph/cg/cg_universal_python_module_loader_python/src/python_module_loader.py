"""Load a Python file and extract a typed instance.

Two entry points:
- load_module: primitive - load a .py file from disk into a Module object.
- load_instance: composes load_module and pulls a single instance of an
  expected type out of the loaded module, either by name or by
  auto-discovery.

Useful for declarative config-as-code: pyproject-style settings, plugin
manifests, architecture descriptions written as dataclass instances. The
caller decides what type to look for; this widget just loads and finds.
"""

import contextlib
import importlib.util
import io
import os
import sys
from typing import Any, Optional, Type, TypeVar


T = TypeVar("T")


class ModuleLoadError(Exception):
    """Base class for all loader failures."""


class ModuleFileNotFoundError(ModuleLoadError):
    """The path does not exist or is not a regular file."""


class ModuleSyntaxError(ModuleLoadError):
    """The file could not be parsed or executed."""


class InstanceNotFoundError(ModuleLoadError):
    """No instance of the expected type was found in the module."""


class InstanceTypeMismatchError(ModuleLoadError):
    """A named variable was found but is not an instance of the expected type."""


class MultipleInstancesError(ModuleLoadError):
    """Auto-discovery found more than one instance and cannot pick one."""


def load_module(
    path: str,
    *,
    module_name: Optional[str] = None,
    suppress_stdout: bool = True,
) -> Any:
    """Load a Python file from disk and return the resulting module.

    Parameters:
        path: Filesystem path to the .py file.
        module_name: Internal sys.modules key. Defaults to the file's
            basename without extension. Pass a unique value to load the
            same path multiple times without cache reuse.
        suppress_stdout: If True, swallow stdout and stderr during module
            execution so noisy top-level prints don't leak.

    Raises:
        ModuleFileNotFoundError: path is missing or not a file.
        ModuleSyntaxError: parse or execution failed.
    """
    if not os.path.isfile(path):
        raise ModuleFileNotFoundError(f"File not found: {path}")

    name = module_name or os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ModuleSyntaxError(f"Could not create module spec for {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module

    sink = io.StringIO()
    try:
        if suppress_stdout:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                spec.loader.exec_module(module)
        else:
            spec.loader.exec_module(module)
    except SyntaxError as e:
        sys.modules.pop(name, None)
        raise ModuleSyntaxError(f"Syntax error in {path}: {e}") from e
    except Exception as e:
        sys.modules.pop(name, None)
        raise ModuleSyntaxError(f"Error executing {path}: {e}") from e

    return module


def load_instance(
    path: str,
    expected_type: Type[T],
    *,
    var_name: Optional[str] = None,
    module_name: Optional[str] = None,
    suppress_stdout: bool = True,
) -> T:
    """Load a Python file and return a single instance of expected_type.

    Two modes:
        var_name given: returns module.<var_name> after type-checking it.
        var_name None: scans module's public attributes for instances of
            expected_type. Exactly one match is required.

    Parameters:
        path: Filesystem path to the .py file.
        expected_type: The dataclass or class to look for.
        var_name: Optional explicit attribute name to extract.
        module_name: Forwarded to load_module.
        suppress_stdout: Forwarded to load_module.

    Raises:
        ModuleFileNotFoundError, ModuleSyntaxError: from load_module.
        InstanceNotFoundError: var_name missing, or auto-discovery found
            zero instances.
        InstanceTypeMismatchError: named var is not an instance of
            expected_type.
        MultipleInstancesError: auto-discovery found more than one
            instance.
    """
    module = load_module(
        path, module_name=module_name, suppress_stdout=suppress_stdout
    )

    if var_name is not None:
        if not hasattr(module, var_name):
            raise InstanceNotFoundError(
                f"{path}: no attribute named {var_name!r}"
            )
        value = getattr(module, var_name)
        if not isinstance(value, expected_type):
            raise InstanceTypeMismatchError(
                f"{path}: {var_name!r} is {type(value).__name__}, "
                f"expected {expected_type.__name__}"
            )
        return value

    matches = []
    for attr in dir(module):
        if attr.startswith("_"):
            continue
        value = getattr(module, attr)
        if isinstance(value, expected_type):
            matches.append((attr, value))

    if not matches:
        raise InstanceNotFoundError(
            f"{path}: no instance of {expected_type.__name__} found"
        )
    if len(matches) > 1:
        names = ", ".join(name for name, _ in matches)
        raise MultipleInstancesError(
            f"{path}: multiple instances of {expected_type.__name__} "
            f"found ({names}); pass var_name to disambiguate"
        )
    return matches[0][1]
