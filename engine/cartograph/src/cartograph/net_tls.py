"""Shared TLS context for every outbound HTTPS call the CLI makes.

stdlib `ssl` verifies against OpenSSL's default cert path. On the python.org
macOS build that path is empty (`ssl.get_default_verify_paths().cafile is None`),
so every registry call fails with CERTIFICATE_VERIFY_FAILED until the user runs
`Install Certificates.command` by hand. The fix already lives in the
`infra-urllib-client-python` widget, which builds a verifying context from the
OS trust store (the macOS keychains, read via the built-in `security` tool).

This module is the single seam that wires that widget into the CLI's own
`urllib.request.urlopen` call sites. Pass `context=registry_ssl_context()` to
every HTTPS request; urlopen ignores the context for plain-http targets.
"""

from cg.infra_urllib_client_python.src.urllib_client import default_ssl_context


def registry_ssl_context():
    """Process-wide verifying SSL context backed by the OS trust store.

    Cached inside the widget, so repeated calls never re-export the keychain.
    """
    return default_ssl_context()
