"""Example usage for OAuth token response normalization."""
from datetime import datetime
from datetime import timezone
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.oauth_token_response import oauth_token_response

record = oauth_token_response(
    {
        "access_token": "example-access-token",
        "refresh_token": "example-refresh-token",
        "expires_at": "2026-05-08T13:00:00Z",
        "scope": "calendar.read calendar.write",
    }
)

print(record.safe_metadata())
print(record.is_expired(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)))
