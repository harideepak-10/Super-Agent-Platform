"""
Read emails tool — fetches emails from Gmail and returns structured data.

Zone: GREEN — runs automatically, no human approval required.

The tool accepts an optional ``gmail_service`` in its constructor so
tests can inject a MockGmailService without touching real credentials.
In production, the service is built lazily from GmailAuth on first use.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 10
_DEFAULT_FILTER = "is:unread -in:spam -in:trash"
_BODY_PREVIEW_CHARS = 200
_FULL_BODY_MAX_CHARS = 3000   # prevent token overflow from large HTML/marketing emails


class ReadEmailsTool(BaseTool):
    """Fetch emails from Gmail and return them as a structured JSON list.

    Input format (JSON string or plain string)::

        {"limit": 10, "filter": "is:unread"}

    If input is not valid JSON, uses defaults (10 unread emails).

    Returns:
        JSON string containing a list of email dicts, each with:
        ``id``, ``subject``, ``sender``, ``date``, ``body_preview``,
        ``full_body``, ``has_attachments``.

        On API failure, returns a JSON dict with ``error`` and
        ``emails`` (empty list) so the agent can handle it gracefully.
    """

    name: str = "read_emails"
    description: str = (
        "Fetches emails from Gmail and returns a structured list. "
        "Input (JSON): {\"limit\": 10, \"filter\": \"is:unread\"}. "
        "Returns a JSON list of emails with id, subject, sender, "
        "date, body_preview, full_body, has_attachments."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, gmail_service: Any = None) -> None:
        """Initialise the tool with an optional pre-built Gmail service.

        Args:
            gmail_service: If provided, used directly for all API calls.
                           If None, a real service is built from GmailAuth
                           on first call to ``run()``.
        """
        self._injected_service = gmail_service
        self._service: Any = None  # lazily built from GmailAuth

    # ------------------------------------------------------------------
    # BaseTool interface
    # ------------------------------------------------------------------

    def run(self, input_str: str) -> str:
        """Fetch emails from Gmail.

        Args:
            input_str: JSON string with optional ``limit`` and ``filter``
                       keys.  Defaults are used if input is invalid JSON.

        Returns:
            JSON string.  On success: a list of email dicts.
            On failure: ``{"error": "...", "emails": []}``.
        """
        limit, query = self._parse_input(input_str)

        try:
            service = self._get_service()
            return self._fetch_emails(service, limit, query)
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Gmail API error while reading emails: {exc}"
            logger.error(error_msg)
            return json.dumps({"error": error_msg, "emails": []})

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_service(self) -> Any:
        """Return the Gmail service, building it from GmailAuth if needed."""
        if self._injected_service is not None:
            return self._injected_service
        if self._service is None:
            from core.tools.gmail.auth import GmailAuth
            self._service = GmailAuth().build_service("default")
        return self._service

    @staticmethod
    def _parse_input(input_str) -> tuple[int, str]:
        """Parse the input (dict or JSON string) into (limit, query)."""
        try:
            data = input_str if isinstance(input_str, dict) else json.loads(input_str)
            limit = int(data.get("limit", data.get("max_results", _DEFAULT_LIMIT)))
            query = str(data.get("filter", _DEFAULT_FILTER))
            return limit, query
        except Exception:
            return _DEFAULT_LIMIT, _DEFAULT_FILTER

    def _fetch_emails(self, service: Any, limit: int, query: str) -> str:
        """Fetch and parse emails from the Gmail API.

        Args:
            service: Gmail API service object.
            limit:   Maximum number of messages to fetch.
            query:   Gmail search query string.

        Returns:
            JSON string containing a list of parsed email dicts.
        """
        # Step 1: List matching message IDs
        result = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=limit)
            .execute()
        )
        messages = result.get("messages", [])

        if not messages:
            return json.dumps([])

        # Step 2: Fetch full details for each message
        emails = []
        for msg_ref in messages:
            try:
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=msg_ref["id"], format="full")
                    .execute()
                )
                emails.append(self._parse_message(msg))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Failed to fetch message {msg_ref['id']}: {exc}")

        return json.dumps(emails, ensure_ascii=False)

    @staticmethod
    def _parse_message(msg: dict[str, Any]) -> dict[str, Any]:
        """Convert a raw Gmail API message dict into our standard format.

        Args:
            msg: Raw message dict from the Gmail API.

        Returns:
            Normalised email dict with standard keys.
        """
        payload = msg.get("payload", {})
        headers = {
            h["name"].lower(): h["value"]
            for h in payload.get("headers", [])
        }

        subject = headers.get("subject", "(no subject)")
        sender = headers.get("from", "(unknown sender)")
        date = headers.get("date", "")

        full_body = ReadEmailsTool._extract_body(payload)
        # Collapse excessive whitespace (marketing emails have hundreds of blank lines)
        import re as _re
        full_body = _re.sub(r"\n{3,}", "\n\n", full_body).strip()
        full_body = full_body[:_FULL_BODY_MAX_CHARS]
        body_preview = full_body[:_BODY_PREVIEW_CHARS].replace("\n", " ")

        attachments = ReadEmailsTool._extract_attachments(payload, msg.get("id", ""))
        has_attachments = len(attachments) > 0

        return {
            "id": msg.get("id", ""),
            "subject": subject,
            "sender": sender,
            "date": date,
            "body_preview": body_preview,
            "full_body": full_body,
            "has_attachments": has_attachments,
            "attachments": attachments,  # [{filename, attachment_id, mime_type, size_bytes}]
        }

    @staticmethod
    def _extract_body(payload: dict[str, Any]) -> str:
        """Recursively extract plain-text body from a Gmail message payload.

        Handles both simple (single-part) and multipart messages.

        Args:
            payload: The ``payload`` section of a Gmail message dict.

        Returns:
            Decoded plain-text body string, or empty string if none found.
        """
        mime_type = payload.get("mimeType", "")

        # Simple message: body data is directly in payload.body.data
        if mime_type == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(
                    data + "=="  # add padding for safety
                ).decode("utf-8", errors="replace")

        # Multipart message: recurse into parts
        if "parts" in payload:
            for part in payload["parts"]:
                text = ReadEmailsTool._extract_body(part)
                if text:
                    return text

        # Fallback: try body.data regardless of mime type
        data = payload.get("body", {}).get("data", "")
        if data:
            try:
                return base64.urlsafe_b64decode(data + "==").decode(
                    "utf-8", errors="replace"
                )
            except Exception:  # noqa: BLE001
                pass

        return ""

    @staticmethod
    def _has_attachments(payload: dict[str, Any]) -> bool:
        """Return True if any part of the message has a non-empty filename."""
        for part in payload.get("parts", []):
            if part.get("filename"):
                return True
        return False

    @staticmethod
    def _extract_attachments(payload: dict[str, Any], message_id: str) -> list[dict[str, Any]]:
        """Extract attachment metadata from a Gmail message payload.

        Returns a list of dicts, each with:
            filename      : original filename
            attachment_id : Gmail attachment ID (pass to download_attachment)
            mime_type     : file MIME type
            size_bytes    : attachment size in bytes
            message_id    : parent message ID (needed by download_attachment)
        """
        attachments = []

        def _walk(parts):
            for part in parts:
                filename = part.get("filename", "")
                body = part.get("body", {})
                attachment_id = body.get("attachmentId", "")

                if filename and attachment_id:
                    attachments.append({
                        "filename":      filename,
                        "attachment_id": attachment_id,
                        "mime_type":     part.get("mimeType", "application/octet-stream"),
                        "size_bytes":    body.get("size", 0),
                        "message_id":    message_id,
                    })

                # Recurse into nested parts
                sub_parts = part.get("parts", [])
                if sub_parts:
                    _walk(sub_parts)

        _walk(payload.get("parts", []))
        return attachments
