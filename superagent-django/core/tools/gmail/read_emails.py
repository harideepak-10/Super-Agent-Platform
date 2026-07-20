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

_DEFAULT_LIMIT        = 1
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
        "IMPORTANT: default limit is 1. Only pass a higher limit if the user explicitly asked for more emails. "
        "Input JSON: {\"limit\": 1, \"filter\": \"-in:spam -in:trash\"}. "
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
        requested, limit, query, date_used = self._parse_input(input_str)
        try:
            service = self._get_service()
            return self._fetch_emails(service, limit, query,
                                      capped=(requested > 10), date_used=date_used)
        except Exception as exc:
            error_msg = f"Gmail API error: {exc}"
            logger.error(error_msg)
            return json.dumps({"error": error_msg, "emails": [], "count": 0})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": {
                "limit":   {"type": "integer", "description": "Number of emails to fetch (default 1, max 10)"},
                "date":    {"type": "string",  "description": "Fetch emails from a specific date. Pass EXACTLY what the user typed — any format works: '14-07-26', '7-7-26', '7/7', '14/07/2026', 'July 14'. The tool converts it automatically."},
                "date_to": {"type": "string",  "description": "End date for a date range (inclusive). Same format flexibility as 'date'. Use together with 'date' for ranges like 13-07-26 to 15-07-26."},
                "filter":  {"type": "string",  "description": "Gmail search filter for non-date queries. Default: '-in:spam -in:trash'. Use 'is:unread -in:spam -in:trash' for unread. Do NOT use this for date queries — use 'date' instead."},
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
    def _parse_date(date_str: str) -> "datetime | None":
        """Parse any user date format into a datetime object.

        Handles: DD-MM-YY, DD/MM/YY, D-M-YY, D-M-YYYY, DD/MM/YYYY,
                 D-M (no year → current year), D/M, 'July 14', 'Jul 14 2026', etc.
        Always treats ambiguous formats as DD-MM (international, not MM-DD).
        """
        from datetime import datetime
        import re

        date_str = date_str.strip()
        now = datetime.now()

        _MONTH_NAMES = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
            "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
            "july": 7, "august": 8, "september": 9, "october": 10,
            "november": 11, "december": 12,
        }

        # "July 14", "14 July", "Jul 14 2026", "14 July 2026"
        m = re.match(
            r'^([a-zA-Z]+)\s+(\d{1,2})(?:\s+(\d{2,4}))?$|'
            r'^(\d{1,2})\s+([a-zA-Z]+)(?:\s+(\d{2,4}))?$',
            date_str
        )
        if m:
            if m.group(1):  # "July 14 ..."
                month = _MONTH_NAMES.get(m.group(1).lower())
                day = int(m.group(2))
                raw_year = m.group(3)
            else:           # "14 July ..."
                day = int(m.group(4))
                month = _MONTH_NAMES.get(m.group(5).lower())
                raw_year = m.group(6)
            if month:
                year = (2000 + int(raw_year)) if raw_year and len(raw_year) == 2 \
                    else (int(raw_year) if raw_year else now.year)
                try:
                    return datetime(year, month, day)
                except ValueError:
                    pass

        # Numeric formats: DD-MM-YYYY, DD/MM/YY, D-M, D/M, etc.
        m = re.match(r'^(\d{1,2})[-/](\d{1,2})(?:[-/](\d{2,4}))?$', date_str)
        if m:
            day, month = int(m.group(1)), int(m.group(2))
            raw_year = m.group(3)
            year = now.year
            if raw_year:
                year = 2000 + int(raw_year) if len(raw_year) == 2 else int(raw_year)
            try:
                return datetime(year, month, day)
            except ValueError:
                # Might be MM-DD — try swapping
                try:
                    return datetime(year, day, month)
                except ValueError:
                    pass

        logger.warning("ReadEmailsTool._parse_date: could not parse %r", date_str)
        return None

    @staticmethod
    def _date_to_gmail_filter(date_str: str, date_to_str: str | None) -> tuple[str, bool]:
        """Convert date string(s) to a Gmail after:/before: filter.
        Returns (filter_string, success).
        """
        from datetime import timedelta
        parsed_from = ReadEmailsTool._parse_date(date_str)
        if not parsed_from:
            return _DEFAULT_FILTER, False

        if date_to_str:
            parsed_to = ReadEmailsTool._parse_date(date_to_str)
            if not parsed_to:
                parsed_to = parsed_from
        else:
            parsed_to = parsed_from

        after  = parsed_from.strftime("%Y/%m/%d")
        before = (parsed_to + __import__("datetime").timedelta(days=1)).strftime("%Y/%m/%d")
        return f"after:{after} before:{before} -in:spam -in:trash", True

    @staticmethod
    def _parse_input(input_str) -> tuple[int, int, str, bool]:
        """Returns (requested_limit, capped_limit, query, date_was_used)."""
        try:
            data = input_str if isinstance(input_str, dict) else json.loads(input_str)

            # --- Date params take priority over manual filter ---
            date_str    = data.get("date", "")
            date_to_str = data.get("date_to", data.get("date_from_to", ""))
            date_used   = False

            if date_str:
                query, date_used = ReadEmailsTool._date_to_gmail_filter(date_str, date_to_str or None)
                # Default limit=10 when date filter is used (date narrows results already)
                default_limit = 10
            else:
                query = str(data.get("filter", data.get("query", _DEFAULT_FILTER)))
                default_limit = _DEFAULT_LIMIT

            requested = int(data.get("limit", data.get("max_results", default_limit)))
            limit = max(1, min(requested, 10))
            return requested, limit, query, date_used
        except Exception:
            return _DEFAULT_LIMIT, _DEFAULT_LIMIT, _DEFAULT_FILTER, False

    def _fetch_emails(self, service: Any, limit: int, query: str, capped: bool = False, date_used: bool = False) -> str:
        result = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=limit)
            .execute()
        )
        messages = result.get("messages", [])

        if not messages:
            if date_used:
                note = f"No emails found for that date (filter: {query})."
            else:
                note = (
                    f"No emails matched filter '{query}'. "
                    "Try search_emails with a different query — do NOT tell the user the inbox is empty."
                )
            return json.dumps({"emails": [], "count": 0, "note": note})

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

        response: dict = {"emails": emails, "count": len(emails)}
        if capped:
            response["note"] = (
                "I can only read up to 10 emails at a time. "
                f"Showing the latest {len(emails)} emails."
            )
        return json.dumps(response, ensure_ascii=False)

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
