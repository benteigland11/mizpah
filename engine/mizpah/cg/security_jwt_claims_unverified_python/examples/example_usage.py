"""Pull an account id out of an id_token the client just received."""

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.jwt_claims_unverified import expires_at, namespaced_claim, unverified_claims


def segment(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


id_token = ".".join([
    segment({"alg": "RS256", "typ": "JWT"}),
    segment({"sub": "user-42", "email": "user@example.org", "exp": 1900000000,
             "https://issuer.example.org/auth": {"account_id": "acct-1234", "plan": "pro"}}),
    "signature-not-checked",
])

claims = unverified_claims(id_token)
print("subject:", claims["sub"])
print("account id:", namespaced_claim(claims, "https://issuer.example.org/auth", "account_id"))
print("expires:", expires_at(claims))
