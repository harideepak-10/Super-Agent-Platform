"""
Read emails tool — fetches emails from Gmail and returns structured data.

Zone: GREEN — runs automatically, no human approval required.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT       = 1
_DEFAULT_FILTER      = "-in:spam -in:trash"
_BODY_PREVIEW_CHARS  = 200
_FULL_BODY_MAX_CHARS = 600

_MONTH_MAP: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5,  "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}


def _local_now() -> datetime:
    """Return the current time in the configured local timezone (default: Asia/Kolkata).

    Falls back to a fixed UTC+5:30 offset if the tzdata package is not installed
    (common on minimal Linux containers like Render before tzdata is in requirements).
    """
    from datetime import timezone as _timezone
    tz_name = os.getenv("EMAIL_DATE_TZ", "Asia/Kolkata")
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        logger.warning("read_emails._local_now: ZoneInfo(%r) failed — falling back to UTC+5:30", tz_name)
        return datetime.now(_timezone(timedelta(hours=5, minutes=30)))


def _parse_date(date_str: str) -> datetime | None:
    """Parse any common date string into a timezone-aware datetime (midnight, local tz).

    Handles:
      Relative: "today", "now", "yesterday", "day before yesterday", "N days ago"
      Month:    "July 14"  "14 July"  "Jul 14 2026"
      Numeric:  D-M  DD-MM-YY  DD-MM-YYYY  (also / and . separators)
    Always treats numeric formats as DD-MM (not MM-DD).
    Returns None if the string cannot be parsed.
    """
    s = date_str.lower().strip()
    # Strip noise words so "yesterday's emails" → "yesterday"
    s = re.sub(r"'s\b|\bemails?\b|\bmails?\b", "", s).strip()

    # Midnight in the user's local timezone — all results are built from this
    local_now = _local_now()
    midnight  = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

    # --- Relative words ---
    if s in ("today", "now"):
        return midnight
    if s == "yesterday":
        return midnight - timedelta(days=1)
    if s in ("day before yesterday", "the day before yesterday"):
        return midnight - timedelta(days=2)
    mo = re.match(r'^(\d{1,3})\s+days?\s+ago$', s)
    if mo:
        return midnight - timedelta(days=int(mo.group(1)))

    def _year(raw: str | None) -> int:
        if not raw:
            return local_now.year
        n = int(raw)
        return 2000 + n if n < 100 else n

    def _make(y: int, month: int, d: int) -> datetime | None:
        try:
            return midnight.replace(year=y, month=month, day=d)
        except ValueError:
            return None

    # --- "July 14", "14 July", "Jul 14 2026", "14 July 2026" ---
    mo = re.match(r'^([a-z]+)\s+(\d{1,2})(?:\s+(\d{2,4}))?$', s)
    if mo:
        month = _MONTH_MAP.get(mo.group(1))
        if month:
            result = _make(_year(mo.group(3)), month, int(mo.group(2)))
            if result:
                return result

    mo = re.match(r'^(\d{1,2})\s+([a-z]+)(?:\s+(\d{2,4}))?$', s)
    if mo:
        month = _MONTH_MAP.get(mo.group(2))
        if month:
            result = _make(_year(mo.group(3)), month, int(mo.group(1)))
            if result:
                return result

    # --- Numeric: D-M, D/M, D.M, DD-MM-YY, DD/MM/YYYY, DD.MM.YYYY, etc. ---
    mo = re.match(r'^(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{2,4}))?$', s)
    if mo:
        d_val  = int(mo.group(1))
        m_val  = int(mo.group(2))
        y_val  = _year(mo.group(3))
        result = _make(y_val, m_val, d_val)   # DD-MM first
        if result:
            return result
        result = _make(y_val, d_val, m_val)   # MM-DD fallback
        if result:
            return result

    logger.warning("read_emails._parse_date: could not parse %r", date_str)
    return None


def _build_gmail_filter(date_str: str, date_to_str: str | None) -> tuple[str | None, bool]:
    """Convert date string(s) to a Gmail after:/before: filter using exact epoch timestamps.

    Epoch-based filters are timezone-exact — no day-buffer needed.
    Returns (filter_string, True) on success, (None, False) on parse failure.
    """
    parsed_from = _parse_date(date_str)
    if not parsed_from:
        logger.warning("read_emails._build_gmail_filter: failed to parse date=%r", date_str)
        return None, False

    parsed_to = _parse_date(date_to_str) if date_to_str else None
    if parsed_to is None or parsed_to < parsed_from:
        parsed_to = parsed_from

    # Use YYYY/MM/DD format — officially supported by Gmail.
    # parsed_from is already midnight in IST so strftime gives the correct local date.
    after  = parsed_from.strftime("%Y/%m/%d")
    before = (parsed_to + timedelta(days=1)).strftime("%Y/%m/%d")
    gmail_filter = f"after:{after} before:{before} -in:spam -in:trash"
    logger.info("read_emails._build_gmail_filter: %s", gmail_filter)
    return gmail_filter, True


class ReadEmailsTool(BaseTool):
    """Fetch emails from Gmail and return structured JSON.

    Input (JSON string or dict)::

        {"limit": 1}
        {"date": "7-7", "limit": 10}
        {"date": "13-07-26", "date_to": "15-07-26", "limit": 10}
        {"filter": "is:unread -in:spam -in:trash", "limit": 5}

    Returns::

        {
            "emails": [...],
            "count": N,
            "note": "..."   (present only when relevant)
        }
    """

    name: str = "read_emails"
    description: str = (
        "Fetch emails from Gmail. "
        "For date requests pass 'date' with exactly what the user typed — any format works. "
        "For date ranges also pass 'date_to'. "
        "For non-date queries use 'filter'. "
        "Default limit is 1 unless user specifies more. "
        "Returns {\"emails\": [...], \"count\": N}."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, gmail_service: Any = None) -> None:
        self._injected_service = gmail_service
        self._service: Any = None

    # ------------------------------------------------------------------
    # BaseTool interface
    # ------------------------------------------------------------------

    def run(self, input_str: Any) -> str:
        logger.info("read_emails.run: input=%r", input_str)
        requested, limit, query, date_used = self._parse_input(input_str)
        logger.info("read_emails.run: limit=%d  query=%r  date_used=%s", limit, query, date_used)

        if isinstance(query, str) and query.startswith("__DATE_PARSE_ERROR__:"):
            bad_date = query[len("__DATE_PARSE_ERROR__:"):]
            logger.warning("read_emails.run: unrecognised date %r — returning error", bad_date)
            return json.dumps({
                "error": (
                    f"Could not understand the date '{bad_date}'. "
                    "Ask the user to give the date like 'yesterday', 'today', '2 days ago', "
                    "'July 14', or '14-07-2026'."
                ),
                "emails": [],
                "count": 0,
            })

        try:
            service = self._get_service()
            return self._fetch_emails(service, limit, query,
                                      capped=(requested > 10), date_used=date_used)
        except Exception as exc:
            logger.error("read_emails.run: Gmail API error: %s", exc)
            return json.dumps({"error": f"Gmail API error: {exc}", "emails": [], "count": 0})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of emails to fetch (default 1, max 10)",
                },
                "date": {
                    "type": "string",
                    "description": (
                        "Fetch emails from a specific date. Pass EXACTLY what the user typed — "
                        "any format works: '14-07-26', '7-7', '7/7', '14/07/2026', 'July 14'."
                    ),
                },
                "date_to": {
                    "type": "string",
                    "description": "End date for a range (inclusive). Same format as 'date'.",
                },
                "filter": {
                    "type": "string",
                    "description": (
                        "Gmail search filter for non-date queries. "
                        "Default: '-in:spam -in:trash'. "
                        "Do NOT use this for date queries — use 'date' instead."
                    ),
                },
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
    def _parse_input(input_str: Any) -> tuple[int, int, str, bool]:
        """Returns (requested_limit, capped_limit, gmail_query, date_was_used)."""
        try:
            data: dict = input_str if isinstance(input_str, dict) else json.loads(input_str)

            date_str    = str(data.get("date",    "") or "").strip()
            date_to_str = str(data.get("date_to", "") or "").strip()

            if date_str:
                query, date_used = _build_gmail_filter(date_str, date_to_str or None)
                if query is None:
                    query = f"__DATE_PARSE_ERROR__:{date_str}"
                # Always fetch 10 for date queries — ignore whatever limit the LLM passed.
                # The LLM defaults to limit=1 from the general description but date searches
                # need the full window to avoid missing emails.
                requested = 10
                limit     = 10
            else:
                query         = str(data.get("filter", data.get("query", _DEFAULT_FILTER)))
                date_used     = False
                requested = int(data.get("limit", data.get("max_results", _DEFAULT_LIMIT)))
                limit     = max(1, min(requested, 50))
            return requested, limit, query, date_used

        except Exception as exc:
            logger.error("read_emails._parse_input error: %s | input=%r", exc, input_str)
            return _DEFAULT_LIMIT, _DEFAULT_LIMIT, _DEFAULT_FILTER, False

    def _fetch_emails(
        self,
        service: Any,
        limit: int,
        query: str,
        capped: bool = False,
        date_used: bool = False,
    ) -> str:
        api_result = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=limit)
            .execute()
        )
        messages = api_result.get("messages", [])
        logger.info("read_emails._fetch_emails: %d messages returned for query=%r", len(messages), query)

        if not messages:
            if date_used:
                note = f"No emails found for that date (filter: {query})."
            else:
                note = (
                    f"No emails matched filter '{query}'. "
                    "Try search_emails with a different query — "
                    "do NOT tell the user the inbox is empty."
                )
            return json.dumps({"emails": [], "count": 0, "note": note})

        emails: list[dict] = []
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
                logger.warning("read_emails._fetch_emails: skipping message %s: %s", msg_ref["id"], exc)

        response: dict = {"emails": emails, "count": len(emails)}
        if capped:
            response["note"] = (
                f"I can only read up to 10 emails at a time. "
                f"Showing the latest {len(emails)} emails."
            )
        return json.dumps(response, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Message parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_message(msg: dict[str, Any]) -> dict[str, Any]:
        payload  = msg.get("payload", {})
        headers  = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

        raw_sender   = headers.get("from", "(unknown sender)")
        sender_name, sender_email = ReadEmailsTool._parse_sender(raw_sender)

        full_body    = ReadEmailsTool._extract_body(payload)
        full_body    = re.sub(r"\n{3,}", "\n\n", full_body).strip()
        full_body    = full_body[:_FULL_BODY_MAX_CHARS]
        body_preview = full_body[:_BODY_PREVIEW_CHARS].replace("\n", " ")

        attachments     = ReadEmailsTool._extract_attachments(payload, msg.get("id", ""))

        return {
            "id":              msg.get("id", ""),
            "thread_id":       msg.get("threadId", ""),
            "subject":         headers.get("subject", "(no subject)"),
            "sender":          raw_sender,
            "sender_name":     sender_name,
            "sender_email":    sender_email,
            "to":              headers.get("to", ""),
            "date":            headers.get("date", ""),
            "body_preview":    body_preview,
            "full_body":       full_body,
            "has_attachments": len(attachments) > 0,
            "attachments":     attachments,
        }

    # ------------------------------------------------------------------
    # Body extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_body(payload: dict[str, Any]) -> str:
        text_plain = ReadEmailsTool._find_part(payload, "text/plain")
        if text_plain:
            return ReadEmailsTool._decode_data(text_plain)

        text_html = ReadEmailsTool._find_part(payload, "text/html")
        if text_html:
            return ReadEmailsTool._strip_html(ReadEmailsTool._decode_data(text_html))

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
        if payload.get("mimeType") == mime_type and payload.get("body", {}).get("data"):
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
        text = re.sub(r"<(style|script)[^>]*>.*?</(style|script)>", "", html,
                      flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = (text
                .replace("&nbsp;",  " ")
                .replace("&amp;",   "&")
                .replace("&lt;",    "<")
                .replace("&gt;",    ">")
                .replace("&quot;",  '"')
                .replace("&#39;",   "'")
                .replace("&mdash;", "—")
                .replace("&ndash;", "–"))
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ------------------------------------------------------------------
    # Sender / attachment parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sender(raw: str) -> tuple[str, str]:
        match = re.match(r'^"?([^"<]+?)"?\s*<([^>]+)>', raw.strip())
        if match:
            return match.group(1).strip(), match.group(2).strip()
        if "@" in raw:
            return raw.strip(), raw.strip()
        return raw.strip(), ""

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
                if part.get("parts"):
                    _walk(part["parts"])

        _walk(payload.get("parts", []))
        return attachments
