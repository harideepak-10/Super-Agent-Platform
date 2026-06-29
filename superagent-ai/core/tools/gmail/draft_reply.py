"""
Draft reply tool — generates a professional email reply from a template.

Zone: GREEN — runs automatically, no human approval required.

No external API or LLM call is made.  Replies are assembled from
structured templates using the provided context.  The returned status
is always "draft" — this tool NEVER sends an email.

A more sophisticated version can call the LLM for open-ended replies
in a later milestone.
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone


# ---------------------------------------------------------------------------
# Reply templates
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, dict[str, str]] = {
    "professional": {
        "greeting":  "Dear {name},",
        "opener":    "Thank you for your email.",
        "closing":   "Please let me know if you need any further information.",
        "sign_off":  "Best regards,\n{agent_name}",
    },
    "friendly": {
        "greeting":  "Hi {name},",
        "opener":    "Thanks for reaching out!",
        "closing":   "Feel free to get in touch if you have any questions.",
        "sign_off":  "Cheers,\n{agent_name}",
    },
    "formal": {
        "greeting":  "Dear {name},",
        "opener":    "I am writing in response to your recent correspondence.",
        "closing":   "Should you require any further assistance, please do not hesitate to contact us.",
        "sign_off":  "Yours sincerely,\n{agent_name}",
    },
}
_DEFAULT_TONE = "professional"
_AGENT_SIGNATURE = "EmailAgent (Draft — awaiting approval)"


class DraftReplyTool(BaseTool):
    """Generate a draft email reply using a tone-matched template.

    Input format (JSON string)::

        {
            "original_email": "Full text of the email being replied to.",
            "context":        "Key points to include in the reply.",
            "tone":           "professional" | "friendly" | "formal"
        }

    ``tone`` defaults to ``"professional"`` if omitted or unrecognised.

    Returns:
        JSON string with keys:
            ``subject``  : str  — reply subject with "Re: " prefix
            ``body``     : str  — complete reply body text
            ``to``       : str  — extracted sender email address
            ``status``   : str  — always ``"draft"``
    """

    name: str = "draft_reply"
    description: str = (
        "Generates a draft email reply using a template. Never sends. "
        "Input JSON: {\"original_email\": \"...\", \"context\": \"...\", "
        "\"tone\": \"professional\"}. "
        "Returns JSON with subject, body, to, status (always 'draft')."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        """Generate a draft reply.

        Args:
            input_str: JSON string with ``original_email``, ``context``,
                       and optional ``tone``.

        Returns:
            JSON string with ``subject``, ``body``, ``to``,
            ``status`` (always ``"draft"``).

        Raises:
            ValueError: If input_str is empty or not parseable.
        """
        if not input_str or not input_str.strip():
            raise ValueError("DraftReplyTool received empty input.")

        params = self._parse_input(input_str)
        original_email: str = params.get("original_email", "")
        context: str = params.get("context", "")
        tone: str = params.get("tone", _DEFAULT_TONE).lower()

        if tone not in _TEMPLATES:
            tone = _DEFAULT_TONE

        template = _TEMPLATES[tone]

        # Extract metadata from the original email
        sender_email = self._extract_sender_email(original_email)
        sender_name = self._extract_sender_name(original_email)
        original_subject = self._extract_subject(original_email)

        # Build reply subject
        subject = self._build_subject(original_subject)

        # Build reply body
        body = self._build_body(template, sender_name, context)

        return json.dumps({
            "subject": subject,
            "body": body,
            "to": sender_email,
            "status": "draft",  # ALWAYS "draft" — never "sent"
        }, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_input(input_str: str) -> dict[str, Any]:
        """Parse the input string into a parameter dict.

        Tries JSON first; if that fails, treats the whole string as
        the ``original_email`` value.

        Args:
            input_str: Raw input from the agent.

        Returns:
            Dict with at least ``original_email`` set.
        """
        stripped = input_str.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
        # Treat raw string as the email to reply to
        return {"original_email": stripped, "context": "", "tone": _DEFAULT_TONE}

    @staticmethod
    def _extract_sender_email(text: str) -> str:
        """Extract the sender's email address from the email text.

        Args:
            text: Full email text.

        Returns:
            Email address string, or ``"unknown@example.com"`` if not found.
        """
        # Match patterns like: From: Name <email@domain.com> or From: email@domain.com
        patterns = [
            r"[Ff]rom:\s*[^<\n]*<([^>]+)>",    # Name <email>
            r"[Ff]rom:\s*([\w.+\-]+@[\w.\-]+)", # bare email
            r"([\w.+\-]+@[\w.\-]+\.[a-zA-Z]{2,})",  # any email in text
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        return "unknown@example.com"

    @staticmethod
    def _extract_sender_name(text: str) -> str:
        """Extract the sender's display name from the email text.

        Args:
            text: Full email text.

        Returns:
            Display name string, or ``"there"`` as a fallback.
        """
        # From: Display Name <email>
        match = re.search(r"[Ff]rom:\s*([^<\n]+?)\s*(?:<|$)", text)
        if match:
            name = match.group(1).strip().strip('"')
            if name and "@" not in name:
                # Return first name only for friendly tone
                return name.split()[0] if name.split() else name
        return "there"

    @staticmethod
    def _extract_subject(text: str) -> str:
        """Extract the subject line from the email text.

        Args:
            text: Full email text.

        Returns:
            Subject string, or ``"your email"`` as a fallback.
        """
        match = re.search(r"[Ss]ubject:\s*(.+)", text)
        if match:
            return match.group(1).strip()
        return "your email"

    @staticmethod
    def _build_subject(original_subject: str) -> str:
        """Build the reply subject, ensuring a single 'Re:' prefix.

        Args:
            original_subject: Original email subject string.

        Returns:
            Subject string prefixed with ``"Re: "``.
        """
        clean = re.sub(r"^(Re:\s*)+", "", original_subject, flags=re.IGNORECASE)
        return f"Re: {clean}"

    @staticmethod
    def _build_body(template: dict[str, str], sender_name: str, context: str) -> str:
        """Assemble the full reply body from template parts and context.

        Args:
            template:    Template dict with greeting, opener, closing,
                         sign_off keys.
            sender_name: Sender's first name (or "there" fallback).
            context:     Agent-provided context to include in the body.

        Returns:
            Complete plain-text email body.
        """
        greeting = template["greeting"].format(name=sender_name)
        opener = template["opener"]
        body_middle = context.strip() if context.strip() else (
            "I have reviewed your message and will get back to you shortly."
        )
        closing = template["closing"]
        sign_off = template["sign_off"].format(agent_name=_AGENT_SIGNATURE)

        return (
            f"{greeting}\n\n"
            f"{opener}\n\n"
            f"{body_middle}\n\n"
            f"{closing}\n\n"
            f"{sign_off}"
        )
