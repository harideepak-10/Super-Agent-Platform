"""
ExtractActionItemsTool — extract action items from emails (GREEN zone).

Parses email content and identifies:
- Tasks assigned to someone
- Deadlines mentioned
- Follow-up requests
- Decisions needed
"""

from __future__ import annotations
import re
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

# Keywords that signal action items
_ACTION_KEYWORDS = [
    r"please\s+\w+",
    r"could you\s+\w+",
    r"can you\s+\w+",
    r"need(s)? to\s+\w+",
    r"action required",
    r"action item",
    r"todo",
    r"to-do",
    r"follow up",
    r"follow-up",
    r"deadline",
    r"by\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)",
    r"by\s+\d{1,2}[\/\-]\d{1,2}",
    r"asap",
    r"urgent",
    r"respond by",
    r"reply by",
    r"get back to",
]

_ACTION_RE = re.compile("|".join(_ACTION_KEYWORDS), re.IGNORECASE)


class ExtractActionItemsTool(BaseTool):
    name = "extract_action_items"
    description = (
        "Extract action items, tasks, and deadlines from email content. "
        "Input: { emails: list[{id, subject, body, from, date}] }. "
        "Returns: { action_items: list[{email_id, subject, from, action, urgency}] }."
    )
    zone = ToolZone.GREEN

    def run(self, tool_input: "str | dict[str, Any]") -> Any:
        if isinstance(tool_input, str):
            try:
                import json
                tool_input = json.loads(tool_input)
            except (json.JSONDecodeError, TypeError):
                tool_input = {}
        emails = tool_input.get("emails", [])
        if not emails:
            return {"error": "emails list is required", "action_items": []}

        action_items = []

        for email in emails:
            email_id = email.get("id", "")
            subject = email.get("subject", "")
            sender = email.get("from", "")
            date = email.get("date", "")
            body = email.get("body", email.get("snippet", ""))

            full_text = f"{subject}\n{body}"
            sentences = re.split(r"[.!?\n]", full_text)

            found_actions = []
            for sentence in sentences:
                sentence = sentence.strip()
                if len(sentence) < 10:
                    continue
                if _ACTION_RE.search(sentence):
                    found_actions.append(sentence)

            # Detect urgency
            urgency = "low"
            lower = full_text.lower()
            if any(w in lower for w in ["asap", "urgent", "immediately", "critical", "emergency"]):
                urgency = "high"
            elif any(w in lower for w in ["today", "tonight", "by eod", "end of day", "deadline"]):
                urgency = "medium"

            if found_actions:
                action_items.append({
                    "email_id": email_id,
                    "subject": subject,
                    "from": sender,
                    "date": date,
                    "actions": found_actions[:5],  # top 5 per email
                    "urgency": urgency,
                })

        return {
            "action_items": action_items,
            "total_emails_scanned": len(emails),
            "emails_with_actions": len(action_items),
        }
