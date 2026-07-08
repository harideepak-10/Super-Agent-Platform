"""
ForwardEmailTool — forward an email to one or more recipients.

Zone: YELLOW — requires human approval before execution.
"""
from __future__ import annotations
import base64
import json
import logging
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class ForwardEmailTool(BaseTool):
    """Forward an email to new recipients (requires approval).

    Zone: YELLOW — BaseAgent raises ApprovalRequired before this runs.

    Input::

        {
            "message_id": "original_message_id",
            "to":         ["recipient@example.com"],
            "note":       "FYI — please see below."   (optional intro text)
        }

    Returns::

        {"status": "forwarded", "to": [...], "subject": "Fwd: ...", "timestamp": "..."}
    """

    name: str = "forward_email"
    description: str = (
        "Forward an email to one or more recipients. REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"message_id\": \"...\", \"to\": [\"...\"], \"note\": \"...(optional)\"}. "
        "Get message_id from read_emails response."
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
        to_list    = data.get("to", [])
        note       = data.get("note", "")

        if not message_id:
            return json.dumps({"error": "'message_id' is required."})
        if not to_list:
            return json.dumps({"error": "'to' list is required."})

        try:
            service = self._get_service()

            # Fetch original message
            original = service.users().messages().get(
                userId="me", id=message_id, format="full",
            ).execute()

            payload = original.get("payload", {})
            headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
            orig_subject = headers.get("subject", "(no subject)")
            orig_from    = headers.get("from", "")
            orig_date    = headers.get("date", "")

            # Extract body
            from core.tools.gmail.read_emails import ReadEmailsTool
            orig_body = ReadEmailsTool._extract_body(payload)

            # Build forwarded message
            fwd_subject = f"Fwd: {orig_subject}" if not orig_subject.startswith("Fwd:") else orig_subject
            fwd_body = ""
            if note:
                fwd_body += f"{note}\n\n"
            fwd_body += (
                f"---------- Forwarded message ----------\n"
                f"From: {orig_from}\n"
                f"Date: {orig_date}\n"
                f"Subject: {orig_subject}\n\n"
                f"{orig_body}"
            )

            mime_msg = MIMEText(fwd_body, "plain", "utf-8")
            mime_msg["to"]      = ", ".join(to_list)
            mime_msg["subject"] = fwd_subject

            raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("ascii")
            result = service.users().messages().send(userId="me", body={"raw": raw}).execute()

            logger.info("ForwardEmailTool: forwarded to %s", to_list)
            return json.dumps({
                "status":     "forwarded",
                "to":         to_list,
                "subject":    fwd_subject,
                "message_id": result.get("id", ""),
                "timestamp":  datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:
            logger.exception("ForwardEmailTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "message_id": {"type": "string"},
                    "to":         {"type": "array", "items": {"type": "string"}},
                    "note":       {"type": "string"},
                },
                "required": ["message_id", "to"],
            },
        }}
