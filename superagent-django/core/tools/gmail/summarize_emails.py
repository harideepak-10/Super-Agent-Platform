"""
SummarizeEmailsTool — summarize a list of emails in one clean pass.

Zone: GREEN — runs automatically, no human approval required.

Accepts the output of read_emails directly:
  Input:  {"emails": [...]}  OR  the emails array directly
  Output: formatted_summary (ready to show the user) + structured summaries
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

# Urgency keyword sets
_CRITICAL_KW = {"critical", "immediately", "asap", "overdue", "final notice", "last chance", "urgent action"}
_HIGH_KW     = {"urgent", "deadline", "payment", "invoice", "action required", "expiring", "important", "due today"}
_MEDIUM_KW   = {"follow up", "follow-up", "waiting", "pending", "please reply", "response needed", "reminder", "checking in"}

_ICON = {"critical": "🚨", "high": "🔴", "medium": "🟡", "low": "🟢"}


class SummarizeEmailsTool(BaseTool):
    """Summarize a list of emails from different senders into one clean report.

    Accepts the result of read_emails directly — either:
      - {"emails": [...], "count": N}  (full read_emails output)
      - {"emails": [...]}
      - [...]  (raw list)

    Returns::

        {
            "formatted_summary": "📧 Email Summary (3 emails)\\n\\n1. John Smith ...",
            "summaries": [...],
            "total": 3,
            "high_urgency_count": 1
        }
    """

    name: str = "summarize_emails"
    description: str = (
        "Summarize emails into a clean numbered report. "
        "Input: {\"emails\": [...]} — pass the 'emails' array from read_emails result. "
        "Returns formatted_summary with sender name, subject, date, urgency, and content summary for each email."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        emails = self._parse_input(input_str)
        if isinstance(emails, str):
            return json.dumps({"error": emails, "summaries": [], "total": 0})

        if not emails:
            return json.dumps({
                "formatted_summary": "No emails to summarize.",
                "summaries": [], "total": 0, "high_urgency_count": 0,
            })

        summaries = [self._summarize_one(i, e) for i, e in enumerate(emails, 1)]
        high_urgency = sum(1 for s in summaries if s["urgency"] in ("high", "critical"))

        return json.dumps({
            "formatted_summary": self._format_output(summaries),
            "summaries":         summaries,
            "total":             len(summaries),
            "high_urgency_count": high_urgency,
        }, ensure_ascii=False)

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": {
                "emails": {
                    "type": "array",
                    "description": "The 'emails' array from read_emails result. Each item has subject, sender, full_body, date, etc.",
                    "items": {"type": "object"},
                },
            }, "required": ["emails"]},
        }}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_input(input_str: Any) -> list | str:
        """Parse input and return the emails list, or an error string."""
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return "Invalid JSON input."

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            emails = data.get("emails", data.get("email_list", []))
            if isinstance(emails, list):
                return emails
        return "Expected {\"emails\": [...]} or a plain list."

    @staticmethod
    def _summarize_one(index: int, email: dict) -> dict:
        raw_sender   = email.get("sender", email.get("from", "Unknown"))
        sender_name  = email.get("sender_name") or SummarizeEmailsTool._clean_name(raw_sender)
        sender_email = email.get("sender_email") or SummarizeEmailsTool._clean_email(raw_sender)
        subject      = email.get("subject", "(no subject)")
        date         = SummarizeEmailsTool._clean_date(email.get("date", ""))
        body         = email.get("full_body") or email.get("body_preview") or email.get("body", "") or email.get("snippet", "")
        urgency      = SummarizeEmailsTool._detect_urgency(subject, body)
        summary      = SummarizeEmailsTool._extract_key_point(body)

        return {
            "index":        index,
            "sender_name":  sender_name,
            "sender_email": sender_email,
            "subject":      subject,
            "date":         date,
            "urgency":      urgency,
            "summary":      summary,
        }

    @staticmethod
    def _format_output(summaries: list[dict]) -> str:
        n     = len(summaries)
        lines = [f"📧 Email Summary — {n} email{'s' if n != 1 else ''}\n"]
        for s in summaries:
            icon     = _ICON.get(s["urgency"], "⚪")
            date_str = f"   📅 {s['date']}\n" if s["date"] else ""
            lines.append(
                f"{s['index']}. {s['sender_name']} <{s['sender_email']}>\n"
                f"   📌 Subject : {s['subject']}\n"
                f"{date_str}"
                f"   {icon} Urgency : {s['urgency'].capitalize()}\n"
                f"   💬 Summary : {s['summary']}\n"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Sender helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_name(raw: str) -> str:
        """Extract display name from 'Name <email>' format."""
        m = re.match(r'^"?([^"<]+?)"?\s*<', raw.strip())
        if m:
            return m.group(1).strip()
        if "@" in raw:
            return raw.split("@")[0].strip()
        return raw.strip()

    @staticmethod
    def _clean_email(raw: str) -> str:
        """Extract email address from 'Name <email>' format."""
        m = re.search(r"<([^>]+)>", raw)
        if m:
            return m.group(1).strip()
        if "@" in raw:
            return raw.strip()
        return ""

    # ------------------------------------------------------------------
    # Date helper
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_date(raw: str) -> str:
        """Return a short readable date like 'Mon, 14 Jul 2026' from raw header."""
        if not raw:
            return ""
        # Many Gmail dates look like: "Mon, 14 Jul 2026 10:30:00 +0530"
        # Just take the first 17 chars which covers "Mon, 14 Jul 2026"
        try:
            parts = raw.strip().split()
            if len(parts) >= 4:
                # "Mon, 14 Jul 2026 ..."  or  "14 Jul 2026 ..."
                return " ".join(parts[:4]).rstrip(",")
        except Exception:
            pass
        return raw[:20]

    # ------------------------------------------------------------------
    # Urgency detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_urgency(subject: str, body: str) -> str:
        text = (subject + " " + body[:600]).lower()
        if any(kw in text for kw in _CRITICAL_KW):
            return "critical"
        if any(kw in text for kw in _HIGH_KW):
            return "high"
        if any(kw in text for kw in _MEDIUM_KW):
            return "medium"
        return "low"

    # ------------------------------------------------------------------
    # Key point extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_key_point(body: str) -> str:
        """Extract a 2-4 sentence summary: topic + key facts + action needed."""
        if not body or not body.strip():
            return "No content available."

        cleaned = re.sub(r"\s+", " ", body).strip()

        # Split on sentence boundaries
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\d\"\'])", cleaned)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 15]

        if not sentences:
            return cleaned[:300] + ("…" if len(cleaned) > 300 else "")

        selected = [sentences[0]]  # always include first sentence

        # Fact sentences — money, dates, IDs, attachments, meetings
        _facts = [
            r"\b(?:₹|Rs\.?|USD|\$|€|£)\s*[\d,]+",
            r"\b\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}\b",
            r"\b(?:due|deadline|by|before|expire|overdue|meeting|call)\b",
            r"\b(?:invoice|order|ref|ticket|#\s*[\w\-]+)\b",
            r"\b(?:attached|attachment|document|file|report|pdf|spreadsheet)\b",
            r"\b(?:payment|amount|total|balance|deposit|refund)\b",
        ]
        for sent in sentences[1:]:
            if any(re.search(p, sent, re.IGNORECASE) for p in _facts):
                if sent not in selected:
                    selected.append(sent)
                if len(selected) >= 3:
                    break

        # Action sentence
        _actions = [
            r"\b(?:please|kindly|could you|can you|need|must|should|require)\b",
            r"\b(?:reply|respond|confirm|approve|review|let me know|follow up|send)\b",
        ]
        for sent in sentences[1:]:
            if any(re.search(p, sent, re.IGNORECASE) for p in _actions):
                if sent not in selected:
                    selected.append(sent)
                break

        result = " ".join(selected[:4])
        if len(result) > 450:
            result = result[:450].rsplit(". ", 1)[0] + "."
        return result
