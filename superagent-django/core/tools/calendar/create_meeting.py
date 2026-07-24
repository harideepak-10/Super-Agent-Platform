"""
CreateMeetingTool — create a Google Calendar event with attendees.

Zone: YELLOW — requires human approval before execution.

Usage:
    "Create a meeting at 11am tomorrow with Arun and Sankar"
    -> agent resolves attendee emails via customer memory
    -> calls this tool with start_time, attendees, title
    -> YELLOW approval gate triggers
    -> after approval: event created, invitations sent
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CreateMeetingTool(BaseTool):
    """Create a Google Calendar event with attendees.

    Input::

        {
            "title":       "Sales review with Arun and Sankar",
            "start_time":  "2026-07-08T11:00:00",     # ISO 8601 local time
            "duration_mins": 60,                        # default: 60
            "attendees":   [
                "arun@example.com",
                "sankar@supplier.com"
            ],
            "description": "Quarterly sales review",   # optional
            "location":    "Google Meet",               # optional
            "timezone":    "Asia/Kolkata"               # default: Asia/Kolkata
        }

    Returns::

        {
            "status":      "created",
            "event_id":    "...",
            "event_url":   "https://calendar.google.com/...",
            "title":       "Sales review with Arun and Sankar",
            "start":       "2026-07-08T11:00:00+05:30",
            "end":         "2026-07-08T12:00:00+05:30",
            "attendees":   ["arun@example.com", "sankar@supplier.com"],
            "meet_link":   "https://meet.google.com/..."   (if Google Meet added)
        }
    """

    name: str = "create_meeting"
    description: str = (
        "Create a Google Calendar event and send invitations to attendees. "
        "REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"title\": \"...\", \"start_time\": \"2026-07-08T11:00:00\", "
        "\"duration_mins\": 60, \"attendees\": [\"email1@...\", \"email2@...\"], "
        "\"description\": \"...(optional)\", \"timezone\": \"Asia/Kolkata\"}. "
        "Use current_time to resolve relative times like 'tomorrow at 11'. "
        "Look up attendee emails via search_customer_by_email if you only have names."
    )
    zone: ToolZone = ToolZone.YELLOW

    def __init__(self, workspace_id: str | None = None, calendar_service: Any = None) -> None:
        self._workspace_id = workspace_id
        self._injected_service = calendar_service

    def _get_service(self) -> Any:
        if self._injected_service is not None:
            return self._injected_service
        if not self._workspace_id:
            raise RuntimeError("No workspace_id — cannot build Calendar service.")
        from apps.integrations.models import Integration
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        integration = Integration.objects.filter(
            workspace_id=self._workspace_id,
            provider=Integration.Provider.GOOGLE_CALENDAR,
            status=Integration.Status.ACTIVE,
        ).first()
        if not integration or not integration.access_token:
            raise RuntimeError(
                "Google Calendar not connected. "
                "Go to Integrations → Google Calendar → Connect."
            )
        creds = Credentials(
            token=integration.access_token,
            refresh_token=integration.refresh_token,
            client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            token_uri="https://oauth2.googleapis.com/token",
            scopes=_CALENDAR_SCOPES,
        )
        return build("calendar", "v3", credentials=creds)

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else (input_str or {})
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        title        = data.get("title", "Meeting")
        start_str    = data.get("start_time", "")
        duration_mins= int(data.get("duration_mins", 60))
        attendees    = data.get("attendees") or []
        description  = data.get("description", "")
        location     = data.get("location", "")
        tz_name      = data.get("timezone", "Asia/Kolkata")

        if not start_str:
            return json.dumps({"error": "'start_time' is required (ISO 8601 format: 2026-07-08T11:00:00)."})

        # Parse start time — handle with or without timezone offset
        try:
            if "+" in start_str or (start_str.endswith("Z")):
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            else:
                # Treat as local time for the given timezone
                start_dt = datetime.fromisoformat(start_str)
                # No tzinfo — pass timezone string to Google as-is
        except ValueError:
            return json.dumps({"error": f"Cannot parse start_time: '{start_str}'. Use ISO 8601 like 2026-07-08T11:00:00"})

        end_dt = start_dt + timedelta(minutes=duration_mins)

        # Format for Google Calendar API
        start_time_str = start_dt.isoformat()
        end_time_str   = end_dt.isoformat()

        # Build the attendees list
        attendee_objs = [{"email": e.strip()} for e in attendees if e.strip()]

        # Build event body
        event_body: dict = {
            "summary":     title,
            "description": description,
            "start": {
                "dateTime": start_time_str,
                "timeZone": tz_name,
            },
            "end": {
                "dateTime": end_time_str,
                "timeZone": tz_name,
            },
            "attendees": attendee_objs,
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email",  "minutes": 30},
                    {"method": "popup",  "minutes": 10},
                ],
            },
        }
        if location:
            event_body["location"] = location

        try:
            service = self._get_service()

            # Try with Google Meet link first; fall back without it if Meet isn't enabled
            meet_link = ""
            try:
                event_body["conferenceData"] = {
                    "createRequest": {
                        "requestId": f"krypsos-{int(datetime.now(timezone.utc).timestamp())}",
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                }
                event = service.events().insert(
                    calendarId="primary",
                    body=event_body,
                    conferenceDataVersion=1,
                    sendUpdates="all",
                ).execute()
                for ep in event.get("conferenceData", {}).get("entryPoints", []):
                    if ep.get("entryPointType") == "video":
                        meet_link = ep.get("uri", "")
                        break
            except Exception:
                # Meet not enabled for this account — create without conference data
                event_body.pop("conferenceData", None)
                event = service.events().insert(
                    calendarId="primary",
                    body=event_body,
                    sendUpdates="all",
                ).execute()

            logger.info(
                "CreateMeetingTool: event created title=%r id=%s attendees=%s",
                title, event.get("id"), [a["email"] for a in attendee_objs],
            )
            return json.dumps({
                "status":    "created",
                "event_id":  event.get("id", ""),
                "event_url": event.get("htmlLink", ""),
                "title":     title,
                "start":     event.get("start", {}).get("dateTime", start_time_str),
                "end":       event.get("end",   {}).get("dateTime", end_time_str),
                "attendees": [a["email"] for a in attendee_objs],
                "meet_link": meet_link,
                "timezone":  tz_name,
            }, ensure_ascii=False)

        except Exception as exc:
            logger.exception("CreateMeetingTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "title":         {"type": "string", "description": "Meeting title / subject"},
                    "start_time":    {"type": "string", "description": "ISO 8601 datetime e.g. 2026-07-08T11:00:00"},
                    "duration_mins": {"type": "integer", "description": "Duration in minutes (default 60)"},
                    "attendees":     {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of attendee email addresses",
                    },
                    "description":   {"type": "string", "description": "Meeting agenda / notes (optional)"},
                    "location":      {"type": "string", "description": "Location or 'Google Meet' (optional)"},
                    "timezone":      {"type": "string", "description": "IANA timezone name (default: Asia/Kolkata)"},
                },
                "required": ["title", "start_time", "attendees"],
            },
        }}
