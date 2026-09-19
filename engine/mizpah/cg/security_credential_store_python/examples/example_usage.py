"""Store, quarantine and re-store a token record in a temporary directory."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.credential_store import CredentialStore, QuarantinedCredential

with tempfile.TemporaryDirectory() as directory:
    store = CredentialStore(Path(directory) / "credentials.json")
    store.put("example-provider", {"access_token": "at-1", "refresh_token": "rt-1"}, {"account": "acct-1"})
    print("listing:", store.list())

    store.quarantine("example-provider", "invalid_grant: refresh token revoked")
    try:
        store.get("example-provider")
    except QuarantinedCredential as error:
        print("blocked:", error)

    store.put("example-provider", {"access_token": "at-2", "refresh_token": "rt-2"}, {"account": "acct-1"})
    print("usable again:", store.get("example-provider").usable)
