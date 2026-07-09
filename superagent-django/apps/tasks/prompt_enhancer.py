"""
Prompt Enhancer — expands short/vague prompts into clear, actionable instructions.

Called automatically before every task is created.
Only applies if the prompt is short (< 12 words) AND matches a known vague pattern.
Longer, specific prompts are always left untouched.

Examples:
  "read email from hostinger"
    → "Read the latest email from Hostinger. Summarise the subject, sender,
       date, and key message in plain English."

  "check my emails"
    → "Read my latest unread emails. For each one, summarise the subject,
       sender, date, and key message in plain English."

  "what's on my calendar"
    → "List my upcoming events for today and tomorrow. Show the title,
       time, location, and attendees for each."
"""

from __future__ import annotations
import re

# Only expand prompts shorter than this word count
_MAX_WORDS_TO_ENHANCE = 12


def enhance(prompt: str, agent_type: str = "") -> str:
    """
    Return an expanded prompt if the input is short and vague.
    Returns the original prompt unchanged if it's already specific enough.
    """
    stripped = prompt.strip()
    word_count = len(stripped.split())

    # Already a detailed prompt — leave it alone
    if word_count >= _MAX_WORDS_TO_ENHANCE:
        return stripped

    lower = stripped.lower()

    # ── Email patterns ─────────────────────────────────────────────────────────

    # "read email from <sender>" / "get email from <sender>"
    m = re.match(
        r"(?:read|get|show|fetch|open)\s+(?:the\s+)?(?:latest\s+)?(?:email|mail|message)\s+from\s+(.+)",
        lower,
    )
    if m:
        sender = m.group(1).strip().rstrip(".")
        return (
            f"Read the latest email from {sender}. "
            f"Summarise the subject, sender, date, and key message in plain English."
        )

    # "read my emails" / "check inbox" / "check emails"
    if re.search(r"(read|check|show|get|fetch|open)\s+(my\s+)?(emails?|inbox|mails?|messages?)", lower):
        return (
            "Read my latest unread emails. "
            "For each one, summarise the subject, sender, date, and key message in plain English."
        )

    # "any new emails" / "do i have new emails"
    if re.search(r"(new|unread|recent)\s+(emails?|mails?|messages?)", lower):
        return (
            "Check my inbox for new unread emails. "
            "List each one with subject, sender, date, and a brief summary of the message."
        )

    # ── Calendar patterns ──────────────────────────────────────────────────────

    # "what's on my calendar" / "check my schedule" / "show my events"
    if re.search(r"(check|show|view|what.?s on|see)\s+(my\s+)?(calendar|schedule|events?|agenda)", lower):
        return (
            "List my upcoming events for today and tomorrow. "
            "Show the title, time, location, and attendees for each."
        )

    # "schedule a meeting" / "book a meeting"
    if re.search(r"(schedule|book|create|set up|arrange)\s+(a\s+)?(meeting|call|event|appointment)", lower):
        return (
            "Find a free 1-hour slot in my calendar for today or tomorrow and "
            "create a meeting with a Google Meet link. Ask me for the attendees if not specified."
        )

    # "free slots" / "when am i free"
    if re.search(r"(free\s+slots?|when\s+am\s+i\s+free|available\s+times?|find\s+time)", lower):
        return (
            "Find all available free time slots in my calendar for today and tomorrow "
            "and list them with their start and end times."
        )

    # ── Document patterns ──────────────────────────────────────────────────────

    # "create a report" / "write a report"
    if re.search(r"(create|write|make|generate|draft)\s+(a\s+)?(report|document|doc|summary|pdf)", lower):
        return (
            f"{stripped}. "
            "Structure it with clear sections, use professional language, "
            "and save it as a PDF unless the user specifies otherwise."
        )

    # "summarise document" / "summarize file"
    if re.search(r"(summarise?|summarize?)\s+(the\s+)?(document|file|pdf|doc|report)", lower):
        return (
            "Summarise the document and extract: "
            "1) A 2–3 sentence overview, "
            "2) Key points as bullet points, "
            "3) Any action items or deadlines mentioned."
        )

    # "translate document" / "translate file"
    m = re.match(r"translate\s+(?:the\s+)?(?:document|file|pdf|doc)\s+(?:to\s+)?(.+)", lower)
    if m:
        lang = m.group(1).strip().rstrip(".")
        return (
            f"Translate the document to {lang}. "
            "Preserve the original formatting and save the output as a Word document."
        )

    # ── Drive patterns ─────────────────────────────────────────────────────────

    # "list my drive files" / "show drive files"
    if re.search(r"(list|show|view)\s+(my\s+)?(drive|google drive)\s+(files?|folders?|documents?)", lower):
        return (
            "List all files in my Google Drive root folder. "
            "Show the file name, type, size, and last modified date for each."
        )

    # No match — return original
    return stripped
