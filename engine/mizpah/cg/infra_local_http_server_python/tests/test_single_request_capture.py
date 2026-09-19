"""Tests for the one-shot request capture."""

import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.single_request_capture import CaptureTimeout, SingleRequestCapture  # noqa: E402


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def test_captures_matching_request_with_query() -> None:
    with SingleRequestCapture("/callback", response_html="<p>hi</p>") as capture:
        assert capture.url.endswith("/callback")
        status, body = _get(capture.url + "?code=abc&state=xyz")
        assert (status, body) == (200, "<p>hi</p>")
        assert capture.wait(timeout=2) == "/callback?code=abc&state=xyz"
        assert capture.captured == "/callback?code=abc&state=xyz"


def test_other_paths_miss_without_ending_capture() -> None:
    with SingleRequestCapture("/callback", miss_html="nope") as capture:
        base = capture.url.rsplit("/callback", 1)[0]
        status, body = _get(base + "/favicon.ico")
        assert (status, body) == (404, "nope")
        assert capture.captured is None
        _get(capture.url + "?code=1")
        assert capture.wait(timeout=2) == "/callback?code=1"


def test_first_request_wins() -> None:
    seen: list[str] = []
    with SingleRequestCapture("/cb", on_request=seen.append) as capture:
        _get(capture.url + "?n=1")
        _get(capture.url + "?n=2")
        assert capture.wait(timeout=2) == "/cb?n=1"
        assert seen == ["/cb?n=1"]


def test_wait_times_out() -> None:
    with SingleRequestCapture("/cb") as capture:
        with pytest.raises(CaptureTimeout):
            capture.wait(timeout=0.05)


def test_wait_from_another_thread() -> None:
    with SingleRequestCapture("/cb") as capture:
        result: list[str] = []
        waiter = threading.Thread(target=lambda: result.append(capture.wait(timeout=5)))
        waiter.start()
        _get(capture.url + "?ok=1")
        waiter.join(timeout=5)
        assert result == ["/cb?ok=1"]


def test_rejects_relative_path() -> None:
    with pytest.raises(ValueError):
        SingleRequestCapture("callback")
