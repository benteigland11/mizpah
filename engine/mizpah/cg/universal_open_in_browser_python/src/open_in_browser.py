"""Open a URL in the operating system's default browser.

A local-first application usually wants to hand the user a window as it
starts. Doing that naively fails in three ways this widget handles:

* **Headless environments.** On a server, over SSH, or in CI there is no
  browser. The stdlib call may block, spawn a text-mode browser, or write to
  stderr. Here it is detected first and reported, not attempted.
* **Unwanted launches.** Automation and ``--no-browser`` flags need a way to
  skip the launch without branching at every call site.
* **Unsafe schemes.** A URL that reaches this function from configuration or
  a request should not be able to launch ``file://`` or a custom handler.
  Only ``http`` and ``https`` are permitted by default.

Every outcome is reported rather than raised, because failing to open a
browser is not a reason to bring down the application that called it.
"""

from __future__ import annotations

import os
import sys
import webbrowser
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlsplit

DEFAULT_ALLOWED_SCHEMES = frozenset({"http", "https"})
DISPLAY_VARIABLES = ("DISPLAY", "WAYLAND_DISPLAY")
HEADLESS_PLATFORM_PREFIXES = ("linux", "freebsd", "openbsd", "netbsd", "sunos", "aix")

OPENED = "opened"
SUPPRESSED = "suppressed"
HEADLESS = "headless"
REJECTED_SCHEME = "rejected-scheme"
NO_BROWSER = "no-browser"

Opener = Callable[[str], bool]


@dataclass(frozen=True)
class LaunchResult:
    """What happened when a URL was handed to the browser.

    ``opened`` is the only field a caller usually needs. ``reason`` explains a
    refusal so the caller can print something useful, such as telling the user
    to open the URL manually.
    """

    opened: bool
    reason: str
    url: str

    @property
    def refused(self) -> bool:
        """True when no browser was launched, for any reason."""
        return not self.opened


def is_headless(
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> bool:
    """True when this environment almost certainly has no browser.

    Only unix-like platforms are inspected, via the display environment
    variables. Windows and macOS always have a default handler, so they are
    never reported headless.
    """
    env = os.environ if environ is None else environ
    system = (sys.platform if platform is None else platform).lower()
    if not system.startswith(HEADLESS_PLATFORM_PREFIXES):
        return False
    return not any(env.get(name) for name in DISPLAY_VARIABLES)


def is_allowed_url(url: str, allowed_schemes: frozenset[str] | set[str] | None = None) -> bool:
    """True when ``url`` uses a scheme that is safe to hand to a browser."""
    allowed = DEFAULT_ALLOWED_SCHEMES if allowed_schemes is None else frozenset(allowed_schemes)
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return False
    return scheme in allowed


def open_url(
    url: str,
    suppress: bool = False,
    allowed_schemes: frozenset[str] | set[str] | None = None,
    opener: Opener | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> LaunchResult:
    """Open ``url`` in the default browser, reporting what happened.

    Never raises for an ordinary failure to launch. Set ``suppress`` to skip
    the launch entirely, which is what a ``--no-browser`` flag should do.
    ``opener`` replaces the launch mechanism, so callers can test the decision
    path without a real browser appearing.
    """
    if suppress:
        return LaunchResult(opened=False, reason=SUPPRESSED, url=url)
    if not is_allowed_url(url, allowed_schemes):
        return LaunchResult(opened=False, reason=REJECTED_SCHEME, url=url)
    if is_headless(environ=environ, platform=platform):
        return LaunchResult(opened=False, reason=HEADLESS, url=url)

    launch = opener or _default_opener
    try:
        launched = bool(launch(url))
    except (OSError, webbrowser.Error):
        launched = False
    return LaunchResult(
        opened=launched,
        reason=OPENED if launched else NO_BROWSER,
        url=url,
    )


def _default_opener(url: str) -> bool:
    return webbrowser.open(url, new=2, autoraise=True)
