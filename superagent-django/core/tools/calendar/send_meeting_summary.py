"""
SendMeetingSummaryTool — email a meeting summary/agenda to all attendees.

Zone: YELLOW — requires human approval before execution.
"""
from __future__ import annotations
import base64
import json
import logging
import os
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class SendMeetingSummaryTool(BaseTool):
    """Email a meeting summary or agenda to all attendees via Gmail.

    Can be used BEFORE a meeting (send agenda) or AFTER (send summary/notes).

    Input::

        {
            "event_id":   "abc123xyz",              # required — fetches attendees automatically
            "summary":    "Here are the key points from today's meeting:\\n- Topic 1\\n- Topic 2",
            "subject":    "Meeting Summary: Q3 Review",  # optional — auto-generated if not given
            "mode":       "summary",                # "summary" (post-meeting) or "agenda" (pre-meeting)
            "action_items": [                        # optional
                "Arun: Send invoice by Friday",
                "Sankar: Review contract draft"
            ],
            "next_meeting": "2026-07-16T11:00:00"   # optional — mention next meeting time
        }

    Returns::

        {
            "status":     "sent",
            "recipients": ["arun@example.com", "sankar@example.com"],
            "subject":    "Meeting Summary: Q3 Review",
            "msg_id":     "..."
        }
    """

    name: str = "send_meeting_summary"
    description: str = (
        "Email a meeting summary or agenda to all attendees via Gmail. "
        "REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"event_id\": \"...\", \"summary\": \"Key points...\", "
        "\"mode\": \"summary\", \"action_items\": [\"Arun: Send invoice\"]}. "
        "Use get_event to find event_id. Mode: 'summary' for post-meeting, 'agenda' for pre-meeting."
    )
    zone: ToolZone = ToolZone.YELLOW

    def __init__(self, workspace_id: str | None = None,
                 calendar_service: Any = None,
                 gmail_service: Any = None) -> None:
        self._workspace_id        = workspace_id
        self._injected_cal        = calendar_service
        self._injected_gmail      = gmail_service

    def _get_calendar_service(self) -> Any:
        if self._injected_cal:
            return self._injected_cal
        if not self._workspace_id:
            raise RuntimeError("No workspace_id provided.")
        from apps.integrations.models import Integration
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        integration = Integration.objects.filter(
            workspace_id=self._workspace_id,
            provider=Integration.Provider.GOOGLE_CALENDAR,
            status=Integration.Status.ACTIVE,
        ).first()
        if not integration or not integration.access_token:
            raise RuntimeError("Google Calendar not connected.")
        creds = Credentials(
            token=integration.access_token,
            refresh_token=integration.refresh_token,
            client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            token_uri="https://oauth2.googleapis.com/token",
        )
        return build("calendar", "v3", credentials=creds)

    def _get_gmail_service(self) -> Any:
        if self._injected_gmail:
            return self._injected_gmail
        if not self._workspace_id:
            raise RuntimeError("No workspace_id provided.")
        from apps.integrations.models import Integration
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        integration = Integration.objects.filter(
            workspace_id=self._workspace_id,
            provider=Integration.Provider.GMAIL,
            status=Integration.Status.ACTIVE,
        ).first()
        if not integration or not integration.access_token:
            raise RuntimeError("Gmail not connected.")
        creds = Credentials(
            token=integration.access_token,
            refresh_token=integration.refresh_token,
            client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            token_uri="https://oauth2.googleapis.com/token",
        )
        return build("gmail", "v1", credentials=creds)

    def _build_email_body(self, mode: str, event_title: str, event_start: str,
                          summary: str, action_items: list, next_meeting: str) -> str:
        lines = []
        if mode == "agenda":
            lines.append(f"Hi team,\n\nHere is the agenda for our upcoming meeting: {event_title}")
            if event_start:
                lines.append(f"📅 Scheduled: {event_start}")
            lines.append(f"\n{summary}")
        else:
            lines.append(f"Hi team,\n\nThank you for joining: {event_title}")
            lines.append(f"\n📋 Meeting Summary:\n{summary}")

        if action_items:
            lines.append("\n✅ Action Items:")
            for item in action_items:
                lines.append(f"  • {item}")

        if next_meeting:
            lines.append(f"\n📅 Next Meeting: {next_meeting}")

        lines.append("\nBest regards,\nKRYPSOS AI Assistant")
        return "\n".join(lines)

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else (input_str or {})
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        event_id     = data.get("event_id", "")
        summary_text = data.get("summary", "")
        subject      = data.get("subject", "")
        mode         = data.get("mode", "summary")
        action_items = data.get("action_items", [])
        next_meeting = data.get("next_meeting", "")

        if not event_id:
            return json.dumps({"error": "'event_id' is required. Use get_event or list_events to find it."})
        if not summary_text:
            return json.dumps({"error": "'summary' is required — provide the meeting notes or agenda."})

        try:
            cal_service   = self._get_calendar_service()
            gmail_service = self._get_gmail_service()

            # Fetch event to get attendees and title
            event = cal_service.events().get(calendarId="primary", eventId=event_id).execute()
            event_title = event.get("summary", "Meeting")
            event_start = event.get("start", {}).get("dateTime", "")

            attendees = [
                a["email"] for a in event.get("attendees", [])
                if not a.get("self", False) and a.get("email")
            ]
            if not attendees:
                return json.dumps({"error": "No attendees found on this event."})

            # Build subject
            if not subject:
                prefix  = "Agenda" if mode == "agenda" else "Meeting Summary"
                subject = f"{prefix}: {event_title}"

            # Build body
            body = self._build_email_body(
                mode, event_title, event_start, summary_text, action_items, next_meeting
            )

            # Send via Gmail
            msg = MIMEMultipart()
            msg["to"]      = ", ".join(attendees)
            msg["subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            raw     = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            result  = gmail_service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()

            logger.info("SendMeetingSummaryTool: sent to %s event_id=%s", attendees, event_id)
            return json.dumps({
                "status":     "sent",
                "recipients": attendees,
                "subject":    subject,
                "msg_id":     result.get("id", ""),
                "mode":       mode,
                "event_title": event_title,
            })

        except Exception as exc:
            logger.exception("SendMeetingSummaryTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "event_id":     {"type": "string", "description": "Google Calendar event ID"},
                "summary":      {"type": "string", "description": "Meeting notes or agenda text"},
                "subject":      {"type": "string", "description": "Email subject (optional, auto-generated)"},
                "mode":         {"type": "string", "enum": ["summary", "agenda"],
                                 "description": "'summary' for post-meeting, 'agenda' for pre-meeting"},
                "action_items": {"type": "array", "items": {"type": "string"},
                                 "description": "List of action items e.g. 'Arun: Send invoice by Friday'"},
                "next_meeting": {"type": "string", "description": "Next meeting datetime ISO 8601 (optional)"},
            }, "required": ["event_id", "summary"]},
        }}
