"""
Google Drive OAuth2 authentication helper.

DriveAuth reads OAuth2 credentials from environment variables and
builds a connected Drive API service object.  Token refresh is
handled automatically when the access token is expired.

Required environment variables:
    DRIVE_CLIENT_ID      — OAuth2 client ID from Google Cloud Console
    DRIVE_CLIENT_SECRET  — OAuth2 client secret
    DRIVE_REFRESH_TOKEN  — Long-lived refresh token obtained during
                           the initial OAuth2 consent flow

Google API packages are imported lazily so the rest of the codebase
can be imported and tested without installing them.
"""

from __future__ import annotations

import os
from typing import Any


_DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",          # full read/write
    "https://www.googleapis.com/auth/drive.readonly",  # read-only fallback
]
_TOKEN_URI = "https://oauth2.googleapis.com/token"


class DriveAuth:
    """Manages Google Drive OAuth2 credentials and API service construction.

    All Google API imports are deferred to method bodies so the module
    can be imported in test environments where the google packages are
    not installed — tests inject a MockDriveService and never call
    these methods.

    Example (production)::

        auth = DriveAuth()
        service = auth.build_service("user_001")
        # service is now a ready-to-use Drive API resource object
    """

    def get_credentials(self, user_id: str) -> Any:
        """Build an OAuth2 Credentials object from environment variables.

        Args:
            user_id: Logical user identifier (reserved for future
                     per-user token storage).

        Returns:
            A ``google.oauth2.credentials.Credentials`` instance.

        Raises:
            EnvironmentError: If any required env var is missing.
            ImportError: If the ``google-auth`` package is not installed.
        """
        self._require_google_auth()

        client_id = os.environ.get("DRIVE_CLIENT_ID")
        client_secret = os.environ.get("DRIVE_CLIENT_SECRET")
        refresh_token = os.environ.get("DRIVE_REFRESH_TOKEN")

        missing = [
            name for name, val in [
                ("DRIVE_CLIENT_ID", client_id),
                ("DRIVE_CLIENT_SECRET", client_secret),
                ("DRIVE_REFRESH_TOKEN", refresh_token),
            ]
            if not val
        ]
        if missing:
            raise EnvironmentError(
                f"Missing Drive credentials in environment variables: "
                f"{', '.join(missing)}.  "
                f"Add them to your .env file before connecting Google Drive."
            )

        from google.oauth2.credentials import Credentials  # type: ignore[import]

        return Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri=_TOKEN_URI,
            scopes=_DRIVE_SCOPES,
        )

    def refresh_token(self, user_id: str) -> Any:
        """Force-refresh the OAuth2 access token.

        Args:
            user_id: Logical user identifier.

        Returns:
            Refreshed ``google.oauth2.credentials.Credentials`` object.
        """
        self._require_google_auth()
        from google.auth.transport.requests import Request  # type: ignore[import]

        creds = self.get_credentials(user_id)
        creds.refresh(Request())
        return creds

    def build_service(self, user_id: str) -> Any:
        """Build and return a connected Drive API service object.

        Args:
            user_id: Logical user identifier.

        Returns:
            A ``googleapiclient.discovery.Resource`` for Drive API v3.
        """
        self._require_google_api()
        from googleapiclient.discovery import build  # type: ignore[import]

        creds = self.get_credentials(user_id)

        if not creds.valid:
            try:
                creds = self.refresh_token(user_id)
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to refresh Drive access token: {exc}"
                ) from exc

        return build("drive", "v3", credentials=creds)

    @staticmethod
    def _require_google_auth() -> None:
        try:
            import google.oauth2.credentials  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The 'google-auth' package is not installed.  "
                "Run: pip install google-auth google-auth-oauthlib"
            ) from exc

    @staticmethod
    def _require_google_api() -> None:
        try:
            import googleapiclient.discovery  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The 'google-api-python-client' package is not installed.  "
                "Run: pip install google-api-python-client"
            ) from exc
