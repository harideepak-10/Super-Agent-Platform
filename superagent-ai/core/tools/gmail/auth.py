"""
Gmail OAuth2 authentication helper.

GmailAuth reads OAuth2 credentials from environment variables and
builds a connected Gmail API service object.  Token refresh is
handled automatically when the access token is expired.

Required environment variables:
    GMAIL_CLIENT_ID      — OAuth2 client ID from Google Cloud Console
    GMAIL_CLIENT_SECRET  — OAuth2 client secret
    GMAIL_REFRESH_TOKEN  — Long-lived refresh token obtained during
                           the initial OAuth2 consent flow

Google API packages are imported lazily so the rest of the codebase
can be imported and tested without installing them.
"""

from __future__ import annotations

import os
from typing import Any


# Gmail API scopes required by this platform
_GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",  # read + send + label
]
_TOKEN_URI = "https://oauth2.googleapis.com/token"


class GmailAuth:
    """Manages Gmail OAuth2 credentials and API service construction.

    All Google API imports are deferred to method bodies so the module
    can be imported in test environments where the google packages are
    not installed — tests inject a MockGmailService and never call
    these methods.

    Example (production)::

        auth = GmailAuth()
        service = auth.build_service("user_001")
        # service is now a ready-to-use Gmail API resource object
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_credentials(self, user_id: str) -> Any:
        """Build an OAuth2 Credentials object from environment variables.

        Args:
            user_id: Logical user identifier (reserved for future
                     per-user token storage; currently all users share
                     the same env-var credentials).

        Returns:
            A ``google.oauth2.credentials.Credentials`` instance.

        Raises:
            EnvironmentError: If any required env var is missing.
            ImportError: If the ``google-auth`` package is not installed.
        """
        self._require_google_auth()

        client_id = os.environ.get("GMAIL_CLIENT_ID")
        client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
        refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN")

        missing = [
            name for name, val in [
                ("GMAIL_CLIENT_ID", client_id),
                ("GMAIL_CLIENT_SECRET", client_secret),
                ("GMAIL_REFRESH_TOKEN", refresh_token),
            ]
            if not val
        ]
        if missing:
            raise EnvironmentError(
                f"Missing Gmail credentials in environment variables: "
                f"{', '.join(missing)}.  "
                f"Add them to your .env file before connecting Gmail."
            )

        from google.oauth2.credentials import Credentials  # type: ignore[import]

        return Credentials(
            token=None,           # will be refreshed on first use
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri=_TOKEN_URI,
            scopes=_GMAIL_SCOPES,
        )

    def refresh_token(self, user_id: str) -> Any:
        """Force-refresh the OAuth2 access token.

        Called automatically by ``build_service`` when the current
        token is expired.

        Args:
            user_id: Logical user identifier (passed to
                     ``get_credentials``).

        Returns:
            Refreshed ``google.oauth2.credentials.Credentials`` object.

        Raises:
            google.auth.exceptions.RefreshError: If the refresh token
                is invalid or has been revoked.
        """
        self._require_google_auth()
        from google.auth.transport.requests import Request  # type: ignore[import]

        creds = self.get_credentials(user_id)
        creds.refresh(Request())
        return creds

    def build_service(self, user_id: str) -> Any:
        """Build and return a connected Gmail API service object.

        The service is ready to make API calls immediately.  If the
        access token is expired it is refreshed automatically before
        the service is constructed.

        Args:
            user_id: Logical user identifier passed to
                     ``get_credentials``.

        Returns:
            A ``googleapiclient.discovery.Resource`` object for the
            Gmail API v1.

        Raises:
            EnvironmentError: If required env vars are missing.
            ImportError: If ``google-api-python-client`` is not installed.
        """
        self._require_google_api()
        from googleapiclient.discovery import build  # type: ignore[import]

        creds = self.get_credentials(user_id)

        # Refresh if token is not yet valid (no access token set)
        if not creds.valid:
            try:
                creds = self.refresh_token(user_id)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"Failed to refresh Gmail access token: {exc}"
                ) from exc

        return build("gmail", "v1", credentials=creds)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_google_auth() -> None:
        """Raise ImportError with a clear message if google-auth is missing."""
        try:
            import google.oauth2.credentials  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The 'google-auth' package is not installed.  "
                "Run: pip install google-auth google-auth-oauthlib"
            ) from exc

    @staticmethod
    def _require_google_api() -> None:
        """Raise ImportError with a clear message if google-api-python-client is missing."""
        try:
            import googleapiclient.discovery  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The 'google-api-python-client' package is not installed.  "
                "Run: pip install google-api-python-client"
            ) from exc
