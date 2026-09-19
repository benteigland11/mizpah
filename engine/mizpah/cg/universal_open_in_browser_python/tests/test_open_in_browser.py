"""Tests for default-browser launching."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.open_in_browser import (  # noqa: E402
    HEADLESS,
    NO_BROWSER,
    OPENED,
    REJECTED_SCHEME,
    SUPPRESSED,
    LaunchResult,
    is_allowed_url,
    is_headless,
    open_url,
)

URL = "http://localhost:8080/"
DESKTOP_ENV = {"DISPLAY": ":0"}
BARE_ENV: dict[str, str] = {}


def recording_opener(calls: list[str], result: bool = True):
    def opener(url: str) -> bool:
        calls.append(url)
        return result

    return opener


def test_opens_a_url_and_reports_success() -> None:
    calls: list[str] = []
    result = open_url(URL, opener=recording_opener(calls), environ=DESKTOP_ENV, platform="linux")

    assert result.opened is True
    assert result.reason == OPENED
    assert result.url == URL
    assert calls == [URL]


def test_suppress_skips_the_launch_entirely() -> None:
    calls: list[str] = []
    result = open_url(URL, suppress=True, opener=recording_opener(calls), environ=DESKTOP_ENV)

    assert result.opened is False
    assert result.reason == SUPPRESSED
    assert calls == []


def test_headless_environment_is_not_attempted() -> None:
    calls: list[str] = []
    result = open_url(URL, opener=recording_opener(calls), environ=BARE_ENV, platform="linux")

    assert result.reason == HEADLESS
    assert calls == []


def test_wayland_display_counts_as_a_desktop() -> None:
    calls: list[str] = []
    result = open_url(
        URL,
        opener=recording_opener(calls),
        environ={"WAYLAND_DISPLAY": "wayland-0"},
        platform="linux",
    )

    assert result.opened is True
    assert calls == [URL]


def test_empty_display_value_is_still_headless() -> None:
    assert is_headless(environ={"DISPLAY": ""}, platform="linux") is True


@pytest.mark.parametrize("platform", ["win32", "darwin", "cygwin"])
def test_desktop_platforms_are_never_headless(platform: str) -> None:
    assert is_headless(environ=BARE_ENV, platform=platform) is False


@pytest.mark.parametrize("platform", ["linux", "freebsd13", "openbsd7", "sunos5"])
def test_unix_platforms_without_a_display_are_headless(platform: str) -> None:
    assert is_headless(environ=BARE_ENV, platform=platform) is True


def test_windows_without_a_display_still_opens() -> None:
    calls: list[str] = []
    result = open_url(URL, opener=recording_opener(calls), environ=BARE_ENV, platform="win32")

    assert result.opened is True
    assert calls == [URL]


@pytest.mark.parametrize(
    "hostile",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "custom-handler://do-something",
        "ftp://example.invalid/x",
        "not a url at all",
    ],
)
def test_unsafe_schemes_are_rejected(hostile: str) -> None:
    calls: list[str] = []
    result = open_url(hostile, opener=recording_opener(calls), environ=DESKTOP_ENV, platform="linux")

    assert result.reason == REJECTED_SCHEME
    assert calls == []


def test_allowed_schemes_can_be_widened_deliberately() -> None:
    calls: list[str] = []
    result = open_url(
        "file:///tmp/report.html",
        allowed_schemes={"file"},
        opener=recording_opener(calls),
        environ=DESKTOP_ENV,
        platform="linux",
    )

    assert result.opened is True
    assert calls == ["file:///tmp/report.html"]


def test_https_is_allowed_by_default() -> None:
    assert is_allowed_url("https://example.invalid/path") is True


def test_scheme_check_is_case_insensitive() -> None:
    assert is_allowed_url("HTTPS://example.invalid") is True


def test_opener_returning_false_reports_no_browser() -> None:
    result = open_url(
        URL, opener=recording_opener([], result=False), environ=DESKTOP_ENV, platform="linux"
    )

    assert result.opened is False
    assert result.reason == NO_BROWSER


def test_opener_raising_does_not_propagate() -> None:
    def exploding(url: str) -> bool:
        raise OSError("no browser binary")

    result = open_url(URL, opener=exploding, environ=DESKTOP_ENV, platform="linux")

    assert result.opened is False
    assert result.reason == NO_BROWSER


def test_refused_is_the_inverse_of_opened() -> None:
    assert LaunchResult(opened=True, reason=OPENED, url=URL).refused is False
    assert LaunchResult(opened=False, reason=HEADLESS, url=URL).refused is True


def test_result_is_immutable() -> None:
    result = LaunchResult(opened=True, reason=OPENED, url=URL)
    with pytest.raises(AttributeError):
        result.opened = False  # type: ignore[misc]


def test_malformed_url_is_rejected_rather_than_raising() -> None:
    assert is_allowed_url("http://[unclosed") is False
