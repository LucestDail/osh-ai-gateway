"""Vertex OAuth token helper."""

from __future__ import annotations

import threading
import time
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2 import service_account

from app.config.settings import settings

_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)
_lock = threading.Lock()
_cached_token: Optional[str] = None
_cached_expiry: float = 0.0


def _load_credentials():
    path = settings.vertex_credentials_path.strip()
    if not path:
        raise RuntimeError("VERTEX_CREDENTIALS_PATH is not set")
    return service_account.Credentials.from_service_account_file(path, scopes=_SCOPES)


def get_vertex_access_token() -> str:
    global _cached_token, _cached_expiry

    with _lock:
        now = time.time()
        if _cached_token and now < _cached_expiry - 60:
            return _cached_token

        creds = _load_credentials()
        creds.refresh(Request())
        _cached_token = creds.token
        expiry = creds.expiry.timestamp() if creds.expiry else now + 3000
        _cached_expiry = expiry
        return _cached_token
