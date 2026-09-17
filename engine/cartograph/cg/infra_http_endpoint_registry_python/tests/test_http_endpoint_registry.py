import types

import pytest

from src import endpoint, get_endpoints


# ---------- decorator basics ----------

def test_decorator_attaches_metadata():
    @endpoint("GET", "/v1/things/{id}")
    def fetch(id): pass
    assert fetch.__endpoint__["method"] == "GET"
    assert fetch.__endpoint__["path"] == "/v1/things/{id}"
    assert fetch.__endpoint__["function"] is fetch


def test_method_uppercased():
    @endpoint("get", "/v1/x")
    def f(): pass
    assert f.__endpoint__["method"] == "GET"


def test_function_still_callable():
    @endpoint("POST", "/v1/x")
    def f(x): return x + 1
    assert f(1) == 2


def test_extra_metadata_passed_through():
    @endpoint("GET", "/v1/x", response="XResponse", tags=["read"], summary="fetch x")
    def f(): pass
    assert f.__endpoint__["response"] == "XResponse"
    assert f.__endpoint__["tags"] == ["read"]
    assert f.__endpoint__["summary"] == "fetch x"


# ---------- validation ----------

def test_rejects_non_string_method():
    with pytest.raises(TypeError):
        endpoint(42, "/v1/x")


def test_rejects_unknown_method():
    with pytest.raises(ValueError):
        endpoint("FETCH", "/v1/x")


def test_rejects_path_without_slash():
    with pytest.raises(ValueError):
        endpoint("GET", "v1/x")


def test_rejects_non_string_path():
    with pytest.raises(ValueError):
        endpoint("GET", None)


def test_decorator_keys_cannot_be_clobbered_by_metadata():
    """A user-supplied `function=` kwarg must not overwrite the function ref."""
    sneaky = object()

    @endpoint("GET", "/x", function=sneaky)
    def real(): return "real"

    assert real.__endpoint__["function"] is real
    assert real.__endpoint__["function"] is not sneaky


def test_decorating_non_callable_raises():
    deco = endpoint("GET", "/x")
    with pytest.raises(TypeError):
        deco("not a function")


# ---------- get_endpoints ----------

def test_get_endpoints_returns_only_decorated():
    mod = types.ModuleType("demo")

    @endpoint("GET", "/a")
    def a(): pass

    @endpoint("POST", "/b")
    def b(): pass

    def undecorated(): pass

    mod.a = a
    mod.b = b
    mod.undecorated = undecorated
    mod.value = 42

    found = get_endpoints(mod)
    paths = [e["path"] for e in found]
    assert "/a" in paths
    assert "/b" in paths
    assert len(found) == 2


def test_get_endpoints_returns_definition_order():
    mod = types.ModuleType("ordered")

    @endpoint("GET", "/first")
    def aaa(): pass

    @endpoint("GET", "/second")
    def zzz(): pass  # name sorts before 'aaa' alphabetically

    @endpoint("GET", "/third")
    def mmm(): pass

    mod.aaa = aaa
    mod.zzz = zzz
    mod.mmm = mmm

    found = get_endpoints(mod)
    paths = [e["path"] for e in found]
    assert paths == ["/first", "/second", "/third"]


def test_get_endpoints_empty_module():
    mod = types.ModuleType("empty")
    assert get_endpoints(mod) == []


def test_get_endpoints_ignores_fake_endpoint_attr():
    """An attribute whose __endpoint__ doesn't reference itself isn't ours."""
    mod = types.ModuleType("fake")

    class Imposter:
        pass
    imp = Imposter()
    imp.__endpoint__ = {"method": "GET", "path": "/x", "function": object()}
    mod.imp = imp

    assert get_endpoints(mod) == []


def test_multiple_endpoints_on_same_module_isolated():
    """Decorating in one module does not pollute another module."""
    mod_a = types.ModuleType("a")
    mod_b = types.ModuleType("b")

    @endpoint("GET", "/a")
    def fa(): pass

    @endpoint("GET", "/b")
    def fb(): pass

    mod_a.fa = fa
    mod_b.fb = fb

    assert len(get_endpoints(mod_a)) == 1
    assert get_endpoints(mod_a)[0]["path"] == "/a"
    assert len(get_endpoints(mod_b)) == 1
    assert get_endpoints(mod_b)[0]["path"] == "/b"


def test_function_object_is_the_decorated_one():
    @endpoint("GET", "/x")
    def f(): return 42
    assert f.__endpoint__["function"] is f
    assert f.__endpoint__["function"]() == 42


# ---------- supported methods ----------

@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def test_all_standard_methods_accepted(method):
    @endpoint(method, "/x")
    def f(): pass
    assert f.__endpoint__["method"] == method
