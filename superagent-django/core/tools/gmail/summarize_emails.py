"""
SummarizeEmailsTool — summarize multiple emails from different senders in one pass.

Zone: GREEN — runs automatically, no human approval required.

Unlike summarize_thread (one conversation between same people), this tool
handles a list of emails from different senders and produces a clean
numbered summary in a single step — no looping, no per-email LLM calls.

Input: the JSON list returned by read_emails.
Output: formatted_summary (ready to show the user) + structured summaries list.
"""

from __future__ import annotations

import json
import re

from core.tools.base_tool import BaseTool, ToolZone


# Keywords used for urgency detection
_CRITICAL_KEYWORDS = {"critical", "immediately", "asap", "overdue", "final notice", "last chance"}
_HIGH_KEYWORDS     = {"urgent", "deadline", "payment", "invoice", "action required", "expiring", "important"}
_MEDIUM_KEYWORDS   = {"follow up", "follow-up", "waiting", "pending", "please reply", "response needed", "reminder"}

_URGENCY_ICON = {
    "critical": "🚨",
    "high":     "🔴",
    "medium":   "🟡",
    "low":      "🟢",
}


class SummarizeEmailsTool(BaseTool):
    """Summarize a list of emails from different senders into one clean report.

    Takes the JSON list produced by ReadEmailsTool and returns:
      - ``formatted_summary`` : ready-to-display text the agent can present directly
      - ``summaries``         : structured list (one entry per email)
      - ``total``             : number of emails processed
      - ``high_urgency_count``: count of high/critical emails

    Input format (JSON string)::

        {
            "emails": [...]     ← list from read_emails
        }

    Or pass the raw list directly.

    Example output::

        📧 Email Summary (3 emails)

        1. Deepak Kumar
           Subject: Q2 Report Review
           🔴 Urgency: High
           Summary: Requesting review of the Q2 report before the board meeting on Friday.

        2. Priya Sharma
           Subject: Invoice #1042 Payment
           🚨 Urgency: Critical
           Summary: Invoice is overdue by 15 days. Requesting immediate payment.

        3. Hari Dev
           Subject: Team Lunch Tomorrow
           🟢 Urgency: Low
           Summary: Organizing a team lunch tomorrow at 1pm. Please confirm attendance.
    """

    name: str = "summarize_emails"
    description: str = (
        "Summarize a list of emails from different senders into a clean numbered report in one step. "
        "Input JSON: {\"emails\": [...]} — the list returned by read_emails. "
        "Returns formatted_summary (ready to show the user), structured summaries per email, "
        "total count, and high_urgency_count. "
        "Use this instead of calling summarize_thread in a loop for each email."
    )
    zone: ToolZone = ToolZone.GREEN

    # ------------------------------------------------------------------
    # BaseTool interface
    # ------------------------------------------------------------------

    def run(self, input_str: str) -> str:
        """Summarize all emails in one pass.

        Args:
            input_str: JSON string with 'emails' list, or a raw JSON list.

        Returns:
            JSON string with formatted_summary, summaries, total, high_urgency_count.
        """
        emails = self._parse_input(input_str)
        if isinstance(emails, str):
            # Error message from _parse_input
            return json.dumps({"error": emails, "summaries": [], "total": 0})

        if not emails:
            return json.dumps({
                "formatted_summary": "No emails to summarize.",
                "summaries": [],
                "total": 0,
                "high_urgency_count": 0,
            })

        summaries = [self._summarize_one(i, email) for i, email in enumerate(emails, 1)]

        formatted = self._format_output(summaries)
        high_urgency = sum(1 for s in summaries if s["urgency"] in ("high", "critical"))

        return json.dumps({
            "formatted_summary": formatted,
            "summaries": summaries,
            "total": len(summaries),
            "high_urgency_count": high_urgency,
        }, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_input(input_str: str):
        """Parse input and return the emails list, or an error string."""
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return "Invalid input. Expected JSON with 'emails' list."

        # Accept {"emails": [...]} or a raw list
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            emails = data.get("emails", [])
            if isinstance(emails, list):
                return emails
        return "Input must be a list of emails or {\"emails\": [...]}."

    @staticmethod
    def _summarize_one(index: int, email: dict) -> dict:
        """Build a summary dict for a single email."""
        sender  = SummarizeEmailsTool._clean_sender(
            email.get("sender", email.get("from", "Unknown"))
        )
        subject = email.get("subject", "(no subject)")
        date    = email.get("date", "")
        body    = email.get("full_body") or email.get("body_preview") or email.get("body", "")

        urgency   = SummarizeEmailsTool._detect_urgency(subject, body)
        key_point = SummarizeEmailsTool._extract_key_point(body)

        return {
            "index":   index,
            "sender":  sender,
            "subject": subject,
            "date":    date,
            "urgency": urgency,
            "summary": key_point,
        }

    @staticmethod
    def _format_output(summaries: list[dict]) -> str:
        """Build the human-readable formatted summary string."""
        lines = [f"📧 Email Summary ({len(summaries)} email{'s' if len(summaries) != 1 else ''})\n"]

        for s in summaries:
            icon = _URGENCY_ICON.get(s["urgency"], "⚪")
            date_str = f"   Date: {s['date']}\n" if s["date"] else ""
            lines.append(
                f"{s['index']}. {s['sender']}\n"
                f"   Subject: {s['subject']}\n"
                f"{date_str}"
                f"   {icon} Urgency: {s['urgency'].capitalize()}\n"
                f"   Summary: {s['summary']}\n"
            )

        return "\n".join(lines)

    @staticmethod
    def _clean_sender(sender: str) -> str:
        """Extract display name from 'Name <email@example.com>' format."""
        match = re.match(r'^"?([^"<]+)"?\s*<', sender.strip())
        if match:
            return match.group(1).strip()
        return sender.strip()

    @staticmethod
    def _detect_urgency(subject: str, body: str) -> str:
        """Classify urgency as critical / high / medium / low from keywords."""
        text = (subject + " " + body[:500]).lower()
        if any(kw in text for kw in _CRITICAL_KEYWORDS):
            return "critical"
        if any(kw in text for kw in _HIGH_KEYWORDS):
            return "high"
        if any(kw in text for kw in _MEDIUM_KEYWORDS):
            return "medium"
        return "low"

    @staticmethod
    def _extract_key_point(body: str) -> str:
        """Extract a proper 2-4 sentence summary covering topic, key facts, and action needed.

        Extracts:
          - What the email is about (topic sentence)
          - Key facts: amounts, dates, deadlines, names, order numbers
          - Any action required from the recipient
        """
        if not body or not body.strip():
            return "No content available."

        # Clean up whitespace and split into sentences
        cleaned = re.sub(r'\s+', ' ', body).strip()
        # Split on sentence-ending punctuation followed by space + capital letter
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z\d"])', cleaned)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

        if not sentences:
            return cleaned[:250] + ("..." if len(cleaned) > 250 else "")

        selected = []

        # 1. Always include the first meaningful sentence (topic)
        selected.append(sentences[0])

        # 2. Look for sentences with key facts: amounts, dates, deadlines, IDs
        _fact_patterns = [
            r'\b(?:₹|Rs\.?|USD|\$|€|£)\s*[\d,]+',         # money
            r'\b\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}\b',     # dates
            r'\b(?:due|deadline|by|before|expire|overdue)\b',
            r'\b(?:invoice|order|reference|ticket|id|#)\s*[\w\-]+',  # IDs
            r'\b(?:meeting|call|appointment|schedule)\b',
            r'\b(?:attached|attachment|document|file|report)\b',
            r'\b(?:payment|amount|total|balance|deposit)\b',
        ]
        for sentence in sentences[1:]:
            s_lower = sentence.lower()
            if any(re.search(p, s_lower) for p in _fact_patterns):
                if sentence not in selected:
                    selected.append(sentence)
                if len(selected) >= 3:
                    break

        # 3. Look for action-required sentence if not already included
        _action_patterns = [
            r'\b(?:please|kindly|could you|can you|request|require|need|must|should)\b',
            r'\b(?:reply|respond|confirm|approve|review|action|let me know|follow up)\b',
        ]
        for sentence in sentences[1:]:
            s_lower = sentence.lower()
            if any(re.search(p, s_lower) for p in _action_patterns):
                if sentence not in selected:
                    selected.append(sentence)
                break

        # Cap at 4 sentences and join
        result = " ".join(selected[:4])

        # Final length guard — keep under 400 chars but don't cut mid-sentence
        if len(result) > 400:
            result = result[:400].rsplit(". ", 1)[0] + "."

        return result

    def to_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "emails": {
                            "type": "array",
                            "description": "List of email objects returned by read_emails.",
                            "items": {"type": "object"},
                        }
                    },
                    "required": ["emails"],
                },
            },
        }
