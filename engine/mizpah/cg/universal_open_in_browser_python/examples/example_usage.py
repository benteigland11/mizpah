"""Example usage of Open In Browser.

Walks the decision path a local app takes at startup. A fake opener stands in
for the real browser so this example never launches a window.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.open_in_browser import is_headless, open_url

APP_URL = "http://localhost:8080/"
DESKTOP = {"DISPLAY": ":0"}
NO_DESKTOP: dict[str, str] = {}

launched: list[str] = []


def fake_opener(url: str) -> bool:
    launched.append(url)
    return True


def show(label: str, result: object) -> None:
    print(f"  {label:<32} {result}")


print("Normal desktop start:")
show("open_url(...)", open_url(APP_URL, opener=fake_opener, environ=DESKTOP, platform="linux"))

print("\nUser passed --no-browser:")
show(
    "open_url(..., suppress=True)",
    open_url(APP_URL, suppress=True, opener=fake_opener, environ=DESKTOP, platform="linux"),
)

print("\nRunning over SSH with no display:")
show("open_url(...)", open_url(APP_URL, opener=fake_opener, environ=NO_DESKTOP, platform="linux"))
show("is_headless()", is_headless(environ=NO_DESKTOP, platform="linux"))

print("\nWindows has a handler even with no display variables:")
show("open_url(...)", open_url(APP_URL, opener=fake_opener, environ=NO_DESKTOP, platform="win32"))

print("\nA URL from config cannot smuggle in another scheme:")
show(
    "open_url('file:///etc/passwd')",
    open_url("file:///etc/passwd", opener=fake_opener, environ=DESKTOP, platform="linux"),
)
show(
    "... unless explicitly allowed",
    open_url(
        "file:///tmp/report.html",
        allowed_schemes={"file"},
        opener=fake_opener,
        environ=DESKTOP,
        platform="linux",
    ),
)

print(f"\nbrowser actually invoked for: {launched}")
