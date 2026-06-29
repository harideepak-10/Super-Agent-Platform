"""
Send email tool — sends an email via the Gmail API after human approval.

Zone: YELLOW — ALWAYS requires human approval before execution.

The YELLOW zone guarantees that BaseAgent raises ApprovalRequired
whenever the LLM selects this tool, so the email is never sent
automatically.  ``run()`` is only called after a human has explicitly
approved the send.

The tool accepts an optional ``gmail_service`` in its constructor so
tests can inject a MockGmailService.  In production the service is
built lazily from GmailAuth.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class SendEmailTool(BaseTool):
    """Send an email via Gmail after explicit human approval.

    Zone: YELLOW — BaseAgent raises ApprovalRequired before this
    tool's ``run()`` is ever called.

    Input format (JSON string)::

        {
            "to":      "recipient@example.com",
            "subject": "Re: Invoice #1042",
            "body":    "Dear John, ..."
        }

    Returns:
        JSON string with keys:
            ``status``     : str  — "sent"
            ``message_id`` : str  — Gmail message ID
            ``timestamp``  : str  — ISO 8601 UTC timestamp
            ``to``         : str  — recipient address
            ``subject``    : str  — email subject
    """

    name: str = "send_email"
    description: str = (
        "Sends an email via Gmail.  ALWAYS requires human approval first "
        "(YELLOW zone).  Input JSON: {\"to\": \"...\", \"subject\": \"...\", "
        "\"body\": \"...\"}. "
        "Returns JSON with status, message_id, timestamp."
    )
    zone: ToolZone = ToolZone.YELLOW  # ← NEVER changes; enforced by BaseAgent

    def __init__(self, gmail_service: Any = None) -> None:
        """Initialise with an optional pre-built Gmail service.

        Args:
            gmail_service: If provided, used directly for API calls.
                           If None, a real service is built from GmailAuth.
        """
        self._injected_service = gmail_service
        self._service: Any = None

    # ------------------------------------------------------------------
    # BaseTool interface
    # ------------------------------------------------------------------

    def run(self, input_str: str) -> str:
        """Send the email (only called after human approval).

        Args:
            input_str: JSON string with ``to``, ``subject``, ``body``.

        Returns:
            JSON string with send result.

        Raises:
            ValueError: If required fields (to, subject, body) are missing.
            RuntimeError: If the Gmail API call fails.
        """
        params = self._parse_and_validate(input_str)
        to: str = params["to"]
        subject: str = params["subject"]
        body: str = params["body"]

        logger.info(f"SendEmailTool: sending email to {to!r} — subject: {subject!r}")

        service = self._get_service()
        raw_message = self._build_raw_message(to, subject, body)

        try:
            result = (
                service.users()
                .messages()
                .send(userId="me", body={"raw": raw_message})
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Gmail API error while sending email: {exc}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from exc

        message_id = result.get("id", "unknown")
        timestamp = datetime.now(timezone.utc).isoformat()

        logger.info(f"SendEmailTool: email sent — message_id={message_id}")

        return json.dumps({
            "status": "sent",
            "message_id": message_id,
            "timestamp": timestamp,
            "to": to,
            "subject": subject,
        })

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_service(self) -> Any:
        """Return the Gmail service, building from GmailAuth if needed."""
        if self._injected_service is not None:
            return self._injected_service
        if self._service is None:
            from core.tools.gmail.auth import GmailAuth
            self._service = GmailAuth().build_service("default")
        return self._service

    @staticmethod
    def _parse_and_validate(input_str: str) -> dict[str, str]:
        """Parse and validate the input JSON.

        Args:
            input_str: Raw input string from the agent.

        Returns:
            Dict with ``to``, ``subject``, ``body`` keys.

        Raises:
            ValueError: If input cannot be parsed or required fields
                        are missing.
        """
        if not input_str or not input_str.strip():
            raise ValueError("SendEmailTool received empty input.")

        try:
            params = json.loads(input_str)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"SendEmailTool expects a JSON string with 'to', 'subject', "
                f"'body'.  Got: {input_str!r}"
            ) from exc

        missing = [f for f in ("to", "subject", "body") if not params.get(f)]
        if missing:
            raise ValueError(
                f"SendEmailTool missing required fields: {missing}"
            )

        return {
            "to": str(params["to"]).strip(),
            "subject": str(params["subject"]).strip(),
            "body": str(params["body"]).strip(),
        }

    @staticmethod
    def _build_raw_message(to: str, subject: str, body: str) -> str:
        """Build a base64url-encoded RFC 2822 message for the Gmail API.

        Args:
            to:      Recipient email address.
            subject: Email subject line.
            body:    Plain-text email body.

        Returns:
            Base64url-encoded string suitable for the Gmail API ``raw``
            field.
        """
        mime_msg = MIMEText(body, "plain", "utf-8")
        mime_msg["to"] = to
        mime_msg["subject"] = subject
        raw_bytes = mime_msg.as_bytes()
        return base64.urlsafe_b64encode(raw_bytes).decode("ascii")
