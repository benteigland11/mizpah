"""Declarative endpoint metadata for HTTP API clients.

Decorate client functions with @endpoint to record their method, path, and
arbitrary extra metadata (response type, request type, tags, description, ...).
The metadata is stored on the function itself as `__endpoint__`, so there is
no shared module-level state to keep in sync.

A downstream consumer (OpenAPI generator, docs generator, test harness) uses
`get_endpoints(module)` to walk a module's API functions in definition order
and read what each one declares.

    from http_endpoint_registry import endpoint, get_endpoints

    @endpoint("GET", "/v1/widgets/{owner}/{widget_id}", response="InspectResponse")
    def inspect(owner, widget_id):
        ...

    @endpoint("POST", "/v1/widgets/{widget_id}/publish",
              response="PushResponse", multipart="PushFields")
    def push(widget_path, widget_id):
        ...

    # Later, in a spec generator:
    import my_client
    for ep in get_endpoints(my_client):
        print(ep["method"], ep["path"], ep.get("response"))

The widget has no opinion on what the metadata keys mean beyond `method` and
`path`. Consumers establish their own conventions for `response`, `request`,
`tags`, etc.
"""

from typing import Any, Callable


_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


def endpoint(method: str, path: str, **metadata: Any) -> Callable:
    """Attach endpoint metadata to a function.

    `method` is uppercased; must be a standard HTTP verb.
    `path` must start with '/'. Placeholders like '{widget_id}' are preserved
    literally and left for the consumer to interpret.
    `**metadata` is stored as-is; consumers choose which keys they care about.

    The keys `method`, `path`, and `function` are set by the decorator itself
    and take precedence over anything in **metadata, so a user-supplied
    `function=...` kwarg cannot overwrite the function reference.
    """
    if not isinstance(method, str):
        raise TypeError("method must be a string")
    normalized = method.upper()
    if normalized not in _ALLOWED_METHODS:
        raise ValueError(
            f"method must be one of {sorted(_ALLOWED_METHODS)}, got {method!r}"
        )
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"path must be a string starting with '/', got {path!r}")

    def decorator(func: Callable) -> Callable:
        if not callable(func):
            raise TypeError("@endpoint can only decorate callables")
        # Spread metadata first so the decorator's own keys win on collision.
        func.__endpoint__ = {
            **metadata,
            "method": normalized,
            "path": path,
            "function": func,
        }
        return func

    return decorator


def get_endpoints(module: Any) -> list[dict]:
    """Return all @endpoint-decorated callables in a module, in definition order.

    Each entry is the `__endpoint__` dict set by the decorator, which contains:
      method   - uppercase HTTP verb
      path     - URL path (unmodified)
      function - the decorated callable
      plus any metadata kwargs passed to @endpoint.

    Definition order is recovered from the function's source line number,
    so entries appear in the order they're defined in the source file.
    Non-callable attributes and callables without __endpoint__ are skipped.
    """
    results: list[dict] = []
    for name in dir(module):
        obj = getattr(module, name, None)
        meta = getattr(obj, "__endpoint__", None)
        if isinstance(meta, dict) and meta.get("function") is obj:
            results.append(meta)
    results.sort(key=_source_line)
    return results


def _source_line(meta: dict) -> int:
    fn = meta.get("function")
    code = getattr(fn, "__code__", None)
    return getattr(code, "co_firstlineno", 0)
