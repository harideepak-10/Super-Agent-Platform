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

Attachments:
    The tool_input may optionally include ``attachment_paths`` — a list of
    dicts with ``path`` and ``filename`` keys.  These are set by the
    PATCH /tasks/<id>/draft/ endpoint before the user approves, so that
    files uploaded via the app are included in the sent email.

    Example tool_input with attachments::

        {
            "to":      "hari@gmail.com",
            "subject": "Invoice",
            "body":    "Please find the invoice attached.",
            "attachment_paths": [
                {"path": "/var/media/attachments/abc/invoice.pdf", "filename": "invoice.pdf"}
            ]
        }
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
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
            "body":    "Dear John, ...",
            "attachment_paths": [          <- optional, set by PATCH /draft/
                {"path": "/abs/path/file.pdf", "filename": "invoice.pdf"}
            ]
        }

    Returns:
        JSON string with keys:
            ``status``       : str  — "sent"
            ``message_id``   : str  — Gmail message ID
            ``timestamp``    : str  — ISO 8601 UTC timestamp
            ``to``           : str  — recipient address
            ``subject``      : str  — email subject
            ``attachments``  : list — filenames that were attached
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
            input_str: JSON string with ``to``, ``subject``, ``body``
                       and optional ``attachment_paths``.

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
        attachment_paths: list[dict] = params.get("attachment_paths") or []

        logger.info(
            f"SendEmailTool: sending email to {to!r} — subject: {subject!r} "
            f"— attachments: {len(attachment_paths)}"
        )

        service = self._get_service()

        if attachment_paths:
            raw_message = self._build_raw_message_with_attachments(
                to, subject, body, attachment_paths
            )
        else:
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
            "status":      "sent",
            "message_id":  message_id,
            "timestamp":   timestamp,
            "to":          to,
            "subject":     subject,
            "attachments": [a.get("filename", "") for a in attachment_paths],
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
    def _parse_and_validate(input_str: str) -> dict:
        """Parse and validate the input JSON.

        Args:
            input_str: Raw input string from the agent.

        Returns:
            Dict with ``to``, ``subject``, ``body`` keys and optional
            ``attachment_paths``.

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

        result = {
            "to":      str(params["to"]).strip(),
            "subject": str(params["subject"]).strip(),
            "body":    str(params["body"]).strip(),
        }

        # Optional attachment_paths — list of {"path": ..., "filename": ...}
        attachment_paths = params.get("attachment_paths")
        if attachment_paths and isinstance(attachment_paths, list):
            # Filter to paths that actually exist on disk
            valid = [
                a for a in attachment_paths
                if isinstance(a, dict) and a.get("path") and os.path.isfile(a["path"])
            ]
            result["attachment_paths"] = valid
        else:
            result["attachment_paths"] = []

        return result

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

    @staticmethod
    def _build_raw_message_with_attachments(
        to: str,
        subject: str,
        body: str,
        attachment_paths: list[dict],
    ) -> str:
        """Build a base64url-encoded multipart MIME message with file attachments.

        Args:
            to:               Recipient email address.
            subject:          Email subject line.
            body:             Plain-text email body.
            attachment_paths: List of dicts with ``path`` and ``filename``.

        Returns:
            Base64url-encoded string suitable for the Gmail API ``raw`` field.
        """
        mime_msg = MIMEMultipart()
        mime_msg["to"] = to
        mime_msg["subject"] = subject
        mime_msg.attach(MIMEText(body, "plain", "utf-8"))

        for att in attachment_paths:
            file_path = att.get("path", "")
            filename = att.get("filename") or os.path.basename(file_path)

            if not file_path or not os.path.isfile(file_path):
                logger.warning(f"SendEmailTool: attachment not found, skipping: {file_path!r}")
                continue

            try:
                with open(file_path, "rb") as fh:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(fh.read())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"',
                )
                mime_msg.attach(part)
                logger.info(f"SendEmailTool: attached {filename!r} from {file_path!r}")
            except OSError as exc:
                logger.error(f"SendEmailTool: could not read attachment {file_path!r}: {exc}")

        return base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("ascii")
