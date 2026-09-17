"""Tests for python_module_loader."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.python_module_loader import (
    InstanceNotFoundError,
    InstanceTypeMismatchError,
    ModuleFileNotFoundError,
    ModuleSyntaxError,
    MultipleInstancesError,
    load_instance,
    load_module,
)


def _write(tmp_path: Path, body: str, name: str = "module.py") -> str:
    p = tmp_path / name
    p.write_text(body)
    return str(p)


def test_load_module_success(tmp_path):
    path = _write(tmp_path, "x = 42\n")
    mod = load_module(path)
    assert mod.x == 42


def test_load_module_missing_file(tmp_path):
    with pytest.raises(ModuleFileNotFoundError):
        load_module(str(tmp_path / "nope.py"))


def test_load_module_syntax_error(tmp_path):
    path = _write(tmp_path, "def broken(:\n")
    with pytest.raises(ModuleSyntaxError):
        load_module(path)


def test_load_module_runtime_error_wrapped(tmp_path):
    path = _write(tmp_path, "raise RuntimeError('boom')\n")
    with pytest.raises(ModuleSyntaxError):
        load_module(path)


def test_load_module_suppresses_stdout_by_default(tmp_path, capsys):
    path = _write(tmp_path, "print('noisy')\n")
    load_module(path)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_load_module_can_emit_stdout_when_disabled(tmp_path, capsys):
    path = _write(tmp_path, "print('noisy')\n", name="noisy.py")
    load_module(path, suppress_stdout=False)
    captured = capsys.readouterr()
    assert "noisy" in captured.out


def test_load_instance_by_name(tmp_path):
    body = "config = {'name': 'hello', 'count': 3}\n"
    path = _write(tmp_path, body, "by_name.py")
    result = load_instance(path, dict, var_name="config")
    assert result == {"name": "hello", "count": 3}


def test_load_instance_named_var_missing(tmp_path):
    body = "config = {'a': 1}\n"
    path = _write(tmp_path, body, "missing_var.py")
    with pytest.raises(InstanceNotFoundError):
        load_instance(path, dict, var_name="other")


def test_load_instance_named_var_wrong_type(tmp_path):
    body = "config = 'just a string'\n"
    path = _write(tmp_path, body, "wrong_type.py")
    with pytest.raises(InstanceTypeMismatchError):
        load_instance(path, dict, var_name="config")


def test_load_instance_auto_discover_single(tmp_path):
    body = "config = complex(1, 2)\n"
    path = _write(tmp_path, body, "auto_single.py")
    result = load_instance(path, complex)
    assert result == complex(1, 2)


def test_load_instance_auto_discover_zero(tmp_path):
    path = _write(tmp_path, "x = 1\n", "auto_zero.py")
    with pytest.raises(InstanceNotFoundError):
        load_instance(path, complex)


def test_load_instance_auto_discover_multiple(tmp_path):
    body = "first = complex(1, 0)\nsecond = complex(0, 1)\n"
    path = _write(tmp_path, body, "auto_many.py")
    with pytest.raises(MultipleInstancesError):
        load_instance(path, complex)


def test_load_instance_ignores_other_types(tmp_path):
    body = "config = {'k': 1}\ndecoy = complex(1, 1)\n"
    path = _write(tmp_path, body, "mixed.py")
    result = load_instance(path, dict, var_name="config")
    assert result == {"k": 1}


def test_load_instance_auto_discover_skips_underscore_names(tmp_path):
    body = "_private = complex(1, 0)\nconfig = complex(0, 1)\n"
    path = _write(tmp_path, body, "underscore.py")
    result = load_instance(path, complex)
    assert result == complex(0, 1)


def test_load_instance_propagates_file_not_found(tmp_path):
    with pytest.raises(ModuleFileNotFoundError):
        load_instance(str(tmp_path / "nope.py"), dict)


def test_load_instance_propagates_syntax_error(tmp_path):
    path = _write(tmp_path, "def broken(:\n", "syntax.py")
    with pytest.raises(ModuleSyntaxError):
        load_instance(path, dict)
