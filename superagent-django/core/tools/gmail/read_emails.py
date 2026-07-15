"""
Read emails tool — fetches emails from Gmail and returns structured data.

Zone: GREEN — runs automatically, no human approval required.

Returns {"emails": [...], "count": N} always so the agent has a consistent
structure to work with. Each email includes id, thread_id, subject, sender,
to, date, body_preview, full_body, has_attachments, attachments.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT        = 10
_DEFAULT_FILTER       = "-in:spam -in:trash"   # ALL emails by default (read + unread)
_BODY_PREVIEW_CHARS   = 200
_FULL_BODY_MAX_CHARS  = 600                     # keep 10-email calls under 6000 TPM Groq limit


class ReadEmailsTool(BaseTool):
    """Fetch emails from Gmail and return them as a structured JSON dict.

    Input format (JSON string)::

        {"limit": 5, "filter": "-in:spam -in:trash"}

    filter defaults to ALL emails (read + unread). Only use "is:unread" when
    the user explicitly asks for unread emails.

    Returns::

        {
            "emails": [
                {
                    "id":              "<gmail_id>",
                    "thread_id":       "<thread_id>",
                    "subject":         "Invoice #1042",
                    "sender":          "John Smith <john@company.com>",
                    "sender_name":     "John Smith",
                    "sender_email":    "john@company.com",
                    "to":              "you@krypsos.tech",
                    "date":            "Mon, 14 Jul 2026 10:30:00 +0530",
                    "body_preview":    "Hi, please review the attached...",
                    "full_body":       "Hi, please review the attached invoice...",
                    "has_attachments": true,
                    "attachments": [
                        {
                            "filename":      "invoice.pdf",
                            "attachment_id": "ANGjdJ...",
                            "message_id":    "<gmail_id>",
                            "mime_type":     "application/pdf",
                            "size_bytes":    49152
                        }
                    ]
                }
            ],
            "count": 1
        }
    """

    name: str = "read_emails"
    description: str = (
        "Fetch emails from Gmail. "
        "Input JSON: {\"limit\": 5, \"filter\": \"-in:spam -in:trash\"}. "
        "Default fetches ALL recent emails (read + unread). "
        "Only use 'is:unread' filter when the user explicitly asks for unread emails. "
        "Returns {\"emails\": [...], \"count\": N}. "
        "Each email has: id, thread_id, subject, sender, sender_name, sender_email, "
        "to, date, body_preview, full_body, has_attachments, attachments[]."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, gmail_service: Any = None) -> None:
        self._injected_service = gmail_service
        self._service: Any = None

    # ------------------------------------------------------------------
    # BaseTool interface
    # ------------------------------------------------------------------

    def run(self, input_str: str) -> str:
        limit, query = self._parse_input(input_str)
        try:
            service = self._get_service()
            return self._fetch_emails(service, limit, query)
        except Exception as exc:
            error_msg = f"Gmail API error: {exc}"
            logger.error(error_msg)
            return json.dumps({"error": error_msg, "emails": [], "count": 0})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": {
                "limit":  {"type": "integer", "description": "Number of emails to fetch (default 10)"},
                "filter": {"type": "string",  "description": "Gmail search filter. Default: '-in:spam -in:trash'. Use 'is:unread -in:spam -in:trash' only for unread."},
            }},
        }}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_service(self) -> Any:
        if self._injected_service is not None:
            return self._injected_service
        if self._service is None:
            from core.tools.gmail.auth import GmailAuth
            self._service = GmailAuth().build_service("default")
        return self._service

    @staticmethod
    def _parse_input(input_str) -> tuple[int, str]:
        try:
            data = input_str if isinstance(input_str, dict) else json.loads(input_str)
            limit = int(data.get("limit", data.get("max_results", _DEFAULT_LIMIT)))
            query = str(data.get("filter", data.get("query", _DEFAULT_FILTER)))
            return limit, query
        except Exception:
            return _DEFAULT_LIMIT, _DEFAULT_FILTER

    def _fetch_emails(self, service: Any, limit: int, query: str) -> str:
        result = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=limit)
            .execute()
        )
        messages = result.get("messages", [])

        if not messages:
            return json.dumps({
                "emails": [],
                "count": 0,
                "note": (
                    f"No emails matched filter '{query}'. "
                    "Try search_emails with a different query — do NOT tell the user the inbox is empty."
                ),
            })

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
            except Exception as exc:
                logger.warning("Failed to fetch message %s: %s", msg_ref["id"], exc)

        return json.dumps({"emails": emails, "count": len(emails)}, ensure_ascii=False)

    @staticmethod
    def _parse_message(msg: dict[str, Any]) -> dict[str, Any]:
        payload = msg.get("payload", {})
        headers = {
            h["name"].lower(): h["value"]
            for h in payload.get("headers", [])
        }

        raw_sender = headers.get("from", "(unknown sender)")
        sender_name, sender_email = ReadEmailsTool._parse_sender(raw_sender)
        subject  = headers.get("subject", "(no subject)")
        date     = headers.get("date", "")
        to_field = headers.get("to", "")

        full_body = ReadEmailsTool._extract_body(payload)
        full_body = re.sub(r"\n{3,}", "\n\n", full_body).strip()
        full_body = full_body[:_FULL_BODY_MAX_CHARS]
        body_preview = full_body[:_BODY_PREVIEW_CHARS].replace("\n", " ")

        attachments   = ReadEmailsTool._extract_attachments(payload, msg.get("id", ""))
        has_attachments = len(attachments) > 0

        return {
            "id":              msg.get("id", ""),
            "thread_id":       msg.get("threadId", ""),
            "subject":         subject,
            "sender":          raw_sender,
            "sender_name":     sender_name,
            "sender_email":    sender_email,
            "to":              to_field,
            "date":            date,
            "body_preview":    body_preview,
            "full_body":       full_body,
            "has_attachments": has_attachments,
            "attachments":     attachments,
        }

    # ------------------------------------------------------------------
    # Body extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_body(payload: dict[str, Any]) -> str:
        """Recursively extract readable text from a Gmail message payload.

        Preference order:
          1. text/plain directly in payload
          2. text/plain in any nested part
          3. text/html stripped of tags (fallback)
        """
        text_plain = ReadEmailsTool._find_part(payload, "text/plain")
        if text_plain:
            return ReadEmailsTool._decode_data(text_plain)

        # Fallback to HTML — strip tags so summary is readable
        text_html = ReadEmailsTool._find_part(payload, "text/html")
        if text_html:
            return ReadEmailsTool._strip_html(ReadEmailsTool._decode_data(text_html))

        # Last resort: decode body.data regardless of mime type
        data = payload.get("body", {}).get("data", "")
        if data:
            try:
                raw = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                return ReadEmailsTool._strip_html(raw)
            except Exception:
                pass

        return ""

    @staticmethod
    def _find_part(payload: dict[str, Any], mime_type: str) -> dict[str, Any] | None:
        """Find the first payload part matching mime_type (recursive)."""
        if payload.get("mimeType", "") == mime_type:
            body_data = payload.get("body", {}).get("data", "")
            if body_data:
                return payload

        for part in payload.get("parts", []):
            found = ReadEmailsTool._find_part(part, mime_type)
            if found:
                return found
        return None

    @staticmethod
    def _decode_data(part: dict[str, Any]) -> str:
        data = part.get("body", {}).get("data", "")
        if not data:
            return ""
        try:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        except Exception:
            return ""

    @staticmethod
    def _strip_html(html: str) -> str:
        """Strip HTML tags and decode common entities."""
        # Remove <style> and <script> blocks entirely
        text = re.sub(r"<(style|script)[^>]*>.*?</(style|script)>", "", html, flags=re.DOTALL | re.IGNORECASE)
        # Remove all remaining tags
        text = re.sub(r"<[^>]+>", " ", text)
        # Decode common HTML entities
        text = (text
                .replace("&nbsp;", " ")
                .replace("&amp;",  "&")
                .replace("&lt;",   "<")
                .replace("&gt;",   ">")
                .replace("&quot;", '"')
                .replace("&#39;",  "'")
                .replace("&mdash;", "—")
                .replace("&ndash;", "–"))
        # Collapse whitespace
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ------------------------------------------------------------------
    # Sender parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sender(raw: str) -> tuple[str, str]:
        """Parse 'Name <email@domain.com>' into (name, email)."""
        match = re.match(r'^"?([^"<]+?)"?\s*<([^>]+)>', raw.strip())
        if match:
            return match.group(1).strip(), match.group(2).strip()
        # Just an email address
        if "@" in raw:
            return raw.strip(), raw.strip()
        return raw.strip(), ""

    # ------------------------------------------------------------------
    # Attachment extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_attachments(payload: dict[str, Any], message_id: str) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []

        def _walk(parts: list) -> None:
            for part in parts:
                filename      = part.get("filename", "")
                body          = part.get("body", {})
                attachment_id = body.get("attachmentId", "")

                if filename and attachment_id:
                    attachments.append({
                        "filename":      filename,
                        "attachment_id": attachment_id,
                        "mime_type":     part.get("mimeType", "application/octet-stream"),
                        "size_bytes":    body.get("size", 0),
                        "message_id":    message_id,
                    })

                sub = part.get("parts", [])
                if sub:
                    _walk(sub)

        _walk(payload.get("parts", []))
        return attachments
