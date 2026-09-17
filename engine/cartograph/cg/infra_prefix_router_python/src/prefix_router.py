"""Longest-prefix-match dispatcher for namespaced keys.

Given a mapping of prefix string to opaque handler value, resolve a key
to the entry whose prefix is the longest matching prefix of that key.
Useful when routing ids across registry tiers, plugin sources, command
families, or any other dotted/dashed namespace where one short prefix
should defer to a longer one.

The router stores opaque handler values; they may be callables,
configs, strings, or any type the caller cares about. Resolution never
invokes them. Callers do that themselves once they have the handler.
"""

from typing import Any, Iterator


class PrefixRouterError(Exception):
    """Base class for prefix-router errors."""


class DuplicatePrefixError(PrefixRouterError):
    """Raised when registering a prefix that is already mapped."""


class NoMatchError(PrefixRouterError):
    """Raised when no prefix matches and no default is configured."""


_SENTINEL = object()


class PrefixRouter:
    """Longest-prefix-match dispatcher.

    The empty string is a valid prefix that matches every key; an
    explicit empty-prefix entry takes precedence over the constructor
    `default`, since it was registered deliberately.
    """

    def __init__(self, default: Any = _SENTINEL) -> None:
        self._routes: dict[str, Any] = {}
        self._default = default

    def register(self, prefix: str, handler: Any) -> None:
        if not isinstance(prefix, str):
            raise TypeError("prefix must be a string")
        if prefix in self._routes:
            raise DuplicatePrefixError(
                f"prefix {prefix!r} is already registered"
            )
        self._routes[prefix] = handler

    def unregister(self, prefix: str) -> Any:
        if prefix not in self._routes:
            raise KeyError(prefix)
        return self._routes.pop(prefix)

    def match(self, key: str) -> tuple[str, Any] | None:
        """Return (prefix, handler) for the longest matching prefix, or None."""
        if not isinstance(key, str):
            raise TypeError("key must be a string")
        best_prefix: str | None = None
        for prefix in self._routes:
            if not key.startswith(prefix):
                continue
            if best_prefix is None or len(prefix) > len(best_prefix):
                best_prefix = prefix
        if best_prefix is None:
            return None
        return best_prefix, self._routes[best_prefix]

    def resolve(self, key: str) -> Any:
        """Return the handler for the longest matching prefix.

        Falls back to the constructor `default` if nothing matches.
        Raises NoMatchError if neither a match nor a default exists.
        """
        hit = self.match(key)
        if hit is not None:
            return hit[1]
        if self._default is _SENTINEL:
            raise NoMatchError(
                f"no prefix matches key {key!r} and no default is set"
            )
        return self._default

    def prefixes(self) -> list[str]:
        """Return registered prefixes ordered longest-first."""
        return sorted(self._routes, key=len, reverse=True)

    def __contains__(self, prefix: str) -> bool:
        return prefix in self._routes

    def __iter__(self) -> Iterator[tuple[str, Any]]:
        for prefix in self.prefixes():
            yield prefix, self._routes[prefix]

    def __len__(self) -> int:
        return len(self._routes)
