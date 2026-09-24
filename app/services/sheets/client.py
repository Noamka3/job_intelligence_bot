"""Authenticated Google Sheets API client.

Uses a service account (GOOGLE_APPLICATION_CREDENTIALS). The
target spreadsheet must be shared with that service account's
client_email (Viewer access is enough) - see README.md for setup.
"""

from __future__ import annotations

from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.core.config import get_settings

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def get_sheets_service() -> Any:
    settings = get_settings()
    credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        settings.google_application_credentials, scopes=_SCOPES
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)
