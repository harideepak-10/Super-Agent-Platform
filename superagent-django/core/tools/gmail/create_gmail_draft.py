"""
CreateGmailDraftTool — save a draft to Gmail's Drafts folder.

Zone: GREEN — runs automatically, no human approval required.
This tool NEVER sends. It only creates a saved draft in Gmail.
The user can review and send it manually from Gmail, or use
reply_to_email / send_email (YELLOW) to send it via the agent.
"""
from __future__ import annotations
import base64
import json
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class CreateGmailDraftTool(BaseTool):
    """Save a draft to Gmail's Drafts folder.

    Input::

        {
            "to":          "recipient@example.com",
            "subject":     "Re: Invoice #1042",
            "body":        "Dear Arun,\\n\\nThank you for your message...",
            "cc":          "accounts@example.com",   (optional)
            "thread_id":   "...",                    (optional — links draft to a thread)
            "reply_to_message_id": "..."             (optional — sets In-Reply-To header)
        }

    Returns::

        {
            "status":   "draft_saved",
            "draft_id": "r1234567890",
            "to":       "recipient@example.com",
            "subject":  "Re: Invoice #1042",
            "gmail_url": "https://mail.google.com/mail/#drafts/r1234567890"
        }
    """

    name: str = "create_gmail_draft"
    description: str = (
        "Save an email as a draft in Gmail's Drafts folder. GREEN zone — no approval needed. "
        "The draft is NOT sent — user reviews and sends from Gmail, or use send_email (YELLOW) to send via agent. "
        "Input JSON: {\"to\": \"...\", \"subject\": \"...\", \"body\": \"...\", "
        "\"cc\": \"...(optional)\", \"thread_id\": \"...(optional)\"}. "
        "Use this after draft_reply to save the generated draft to Gmail."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, gmail_service: Any = None, workspace_id: str | None = None) -> None:
        self._injected_service = gmail_service
        self._workspace_id     = workspace_id
        self._service: Any     = None

    def _get_service(self) -> Any:
        if self._injected_service is not None:
            return self._injected_service
        if self._service is not None:
            return self._service
        if not self._workspace_id:
            raise RuntimeError("No Gmail service or workspace_id provided.")
        import os
        from apps.integrations.models import Integration
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        integration = Integration.objects.filter(
            workspace_id=self._workspace_id,
            provider=Integration.Provider.GMAIL,
            status=Integration.Status.ACTIVE,
        ).first()
        if not integration or not integration.access_token:
            raise RuntimeError("Gmail not connected. Go to Integrations → Gmail → Connect.")
        creds = Credentials(
            token=integration.access_token,
            refresh_token=integration.refresh_token,
            client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            token_uri="https://oauth2.googleapis.com/token",
        )
        self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        to        = data.get("to", "")
        subject   = data.get("subject", "(no subject)")
        body      = data.get("body", "")
        cc        = data.get("cc", "")
        thread_id = data.get("thread_id", "")
        reply_to_msg_id = data.get("reply_to_message_id", "")

        if not to:
            return json.dumps({"error": "'to' is required."})

        try:
            service = self._get_service()
        except RuntimeError as exc:
            return json.dumps({"error": str(exc)})

        try:
            # Build MIME message
            msg = MIMEMultipart("alternative")
            msg["to"]      = to
            msg["subject"] = subject
            if cc:
                msg["cc"] = cc
            if reply_to_msg_id:
                msg["In-Reply-To"]  = reply_to_msg_id
                msg["References"]   = reply_to_msg_id

            msg.attach(MIMEText(body, "plain"))

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

            draft_body: dict = {"message": {"raw": raw}}
            if thread_id:
                draft_body["message"]["threadId"] = thread_id

            result = service.users().drafts().create(
                userId="me", body=draft_body
            ).execute()

            draft_id  = result.get("id", "")
            gmail_url = f"https://mail.google.com/mail/#drafts/{draft_id}" if draft_id else ""

            logger.info("CreateGmailDraftTool: saved draft to=%r subject=%r id=%s", to, subject, draft_id)
            return json.dumps({
                "status":    "draft_saved",
                "draft_id":  draft_id,
                "to":        to,
                "subject":   subject,
                "gmail_url": gmail_url,
                "note":      "Draft saved to Gmail. It has NOT been sent. Open Gmail to review and send.",
            }, ensure_ascii=False)

        except Exception as exc:
            logger.exception("CreateGmailDraftTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "to":                   {"type": "string", "description": "Recipient email address"},
                    "subject":              {"type": "string"},
                    "body":                 {"type": "string"},
                    "cc":                   {"type": "string", "description": "CC email address (optional)"},
                    "thread_id":            {"type": "string", "description": "Gmail thread ID to link draft to (optional)"},
                    "reply_to_message_id":  {"type": "string", "description": "Message-ID of email being replied to (optional)"},
                },
                "required": ["to", "subject", "body"],
            },
        }}
