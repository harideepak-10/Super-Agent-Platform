"""
ReplyToEmailTool — send a reply to an existing email thread.

Zone: YELLOW — requires human approval before execution.
"""
from __future__ import annotations
import base64
import json
import logging
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class ReplyToEmailTool(BaseTool):
    """Send a reply to an existing Gmail thread (requires approval).

    Zone: YELLOW — BaseAgent raises ApprovalRequired before this runs.

    Input::

        {
            "message_id":  "original_message_id",   ← from read_emails
            "thread_id":   "thread_id",              ← from read_emails (optional if message_id given)
            "to":          "sender@example.com",     ← usually the original sender
            "subject":     "Re: Original Subject",
            "body":        "Your reply text here",
            "cc":          ["cc@example.com"]        ← optional
        }

    Returns::

        {"status": "sent", "message_id": "...", "thread_id": "...", "timestamp": "..."}
    """

    name: str = "reply_to_email"
    description: str = (
        "Send a reply to an existing email thread. REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"message_id\": \"...\", \"to\": \"...\", \"subject\": \"Re: ...\", \"body\": \"...\"}. "
        "Get message_id and thread_id from read_emails. "
        "Use draft_reply first to compose the reply, then call this after approval."
    )
    zone: ToolZone = ToolZone.YELLOW

    def __init__(self, gmail_service: Any = None) -> None:
        self._injected_service = gmail_service
        self._service: Any = None

    def _get_service(self) -> Any:
        if self._injected_service is not None:
            return self._injected_service
        if self._service is None:
            from core.tools.gmail.auth import GmailAuth
            self._service = GmailAuth().build_service("default")
        return self._service

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        message_id = data.get("message_id", "")
        thread_id  = data.get("thread_id", "")
        to         = data.get("to", "")
        subject    = data.get("subject", "")
        body       = data.get("body", "")
        cc         = data.get("cc", [])

        if not to or not body:
            return json.dumps({"error": "'to' and 'body' are required."})

        # Ensure subject starts with Re:
        if subject and not subject.lower().startswith("re:"):
            subject = "Re: " + subject

        try:
            service = self._get_service()

            # If we only have message_id, fetch thread_id from the message
            if not thread_id and message_id:
                msg = service.users().messages().get(
                    userId="me", id=message_id, format="metadata",
                    metadataHeaders=["Subject", "Message-ID"],
                ).execute()
                thread_id = msg.get("threadId", "")

            mime_msg = MIMEText(body, "plain", "utf-8")
            mime_msg["to"]      = to
            mime_msg["subject"] = subject
            if cc:
                mime_msg["cc"] = ", ".join(cc)
            if message_id:
                mime_msg["In-Reply-To"] = message_id
                mime_msg["References"]  = message_id

            raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("ascii")
            send_body = {"raw": raw}
            if thread_id:
                send_body["threadId"] = thread_id

            result = service.users().messages().send(userId="me", body=send_body).execute()
            timestamp = datetime.now(timezone.utc).isoformat()

            logger.info("ReplyToEmailTool: reply sent to %s", to)
            return json.dumps({
                "status":     "sent",
                "message_id": result.get("id", ""),
                "thread_id":  result.get("threadId", thread_id),
                "timestamp":  timestamp,
                "to":         to,
                "subject":    subject,
            })
        except Exception as exc:
            logger.exception("ReplyToEmailTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "message_id": {"type": "string"},
                    "thread_id":  {"type": "string"},
                    "to":         {"type": "string"},
                    "subject":    {"type": "string"},
                    "body":       {"type": "string"},
                    "cc":         {"type": "array", "items": {"type": "string"}},
                },
                "required": ["to", "body"],
            },
        }}
