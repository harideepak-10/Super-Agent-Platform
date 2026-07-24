"""
UpdateEventTool — update/reschedule an existing Google Calendar event.

Zone: YELLOW — requires human approval before execution.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class UpdateEventTool(BaseTool):
    """Update or reschedule an existing Google Calendar event.

    Input::

        {
            "event_id":      "abc123xyz",        # required (use get_event or list_events)
            "title":         "New title",         # optional — update title
            "start_time":    "2026-07-10T14:00:00", # optional — reschedule
            "duration_mins": 90,                  # optional — change duration
            "description":   "Updated agenda",    # optional
            "location":      "Office",            # optional
            "add_attendees": ["new@example.com"], # optional — add new attendees
            "timezone":      "Asia/Kolkata"       # default: Asia/Kolkata
        }

    Returns::

        {
            "status":    "updated",
            "event_id":  "abc123xyz",
            "event_url": "https://calendar.google.com/...",
            "title":     "New title",
            "start":     "2026-07-10T14:00:00+05:30",
            "end":       "2026-07-10T15:30:00+05:30"
        }
    """

    name: str = "update_event"
    description: str = (
        "Update or reschedule an existing Google Calendar event. "
        "REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"event_id\": \"...\", \"start_time\": \"2026-07-10T14:00:00\", "
        "\"duration_mins\": 60, \"title\": \"New title (optional)\"}. "
        "Use get_event or list_events to find the event_id first."
    )
    zone: ToolZone = ToolZone.YELLOW

    def __init__(self, workspace_id: str | None = None, calendar_service: Any = None) -> None:
        self._workspace_id     = workspace_id
        self._injected_service = calendar_service

    def _get_service(self) -> Any:
        if self._injected_service:
            return self._injected_service
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

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else (input_str or {})
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        event_id     = data.get("event_id", "")
        title        = data.get("title", "")
        start_str    = data.get("start_time", "")
        duration_mins= int(data.get("duration_mins", 0))
        description  = data.get("description", "")
        location     = data.get("location", "")
        add_attendees= data.get("add_attendees", [])
        tz_name      = data.get("timezone", "Asia/Kolkata")

        if not event_id:
            return json.dumps({"error": "'event_id' is required. Use get_event or list_events to find it."})

        try:
            service = self._get_service()

            # Fetch existing event
            event = service.events().get(calendarId="primary", eventId=event_id).execute()

            # Apply updates
            if title:
                event["summary"] = title
            if description:
                event["description"] = description
            if location:
                event["location"] = location

            if start_str:
                try:
                    start_dt = datetime.fromisoformat(start_str)
                except ValueError:
                    return json.dumps({"error": f"Cannot parse start_time: '{start_str}'"})

                # If duration_mins given, use it; else keep original duration
                if duration_mins > 0:
                    end_dt = start_dt + timedelta(minutes=duration_mins)
                else:
                    # Try to keep original duration
                    orig_start_str = event.get("start", {}).get("dateTime", "")
                    orig_end_str   = event.get("end",   {}).get("dateTime", "")
                    if orig_start_str and orig_end_str:
                        orig_start = datetime.fromisoformat(orig_start_str)
                        orig_end   = datetime.fromisoformat(orig_end_str)
                        orig_dur   = orig_end - orig_start
                        end_dt     = start_dt + orig_dur
                    else:
                        end_dt = start_dt + timedelta(hours=1)

                event["start"] = {"dateTime": start_dt.isoformat(), "timeZone": tz_name}
                event["end"]   = {"dateTime": end_dt.isoformat(),   "timeZone": tz_name}

            elif duration_mins > 0:
                # Change only duration, keep start
                orig_start_str = event.get("start", {}).get("dateTime", "")
                if orig_start_str:
                    orig_start = datetime.fromisoformat(orig_start_str)
                    end_dt     = orig_start + timedelta(minutes=duration_mins)
                    event["end"] = {"dateTime": end_dt.isoformat(), "timeZone": tz_name}

            # Add new attendees (preserve existing)
            if add_attendees:
                existing_emails = {a["email"] for a in event.get("attendees", [])}
                for email in add_attendees:
                    if email.strip() and email.strip() not in existing_emails:
                        event.setdefault("attendees", []).append({"email": email.strip()})

            updated = service.events().update(
                calendarId="primary",
                eventId=event_id,
                body=event,
                sendUpdates="all",
            ).execute()

            logger.info("UpdateEventTool: event updated id=%s", event_id)
            return json.dumps({
                "status":    "updated",
                "event_id":  updated.get("id", ""),
                "event_url": updated.get("htmlLink", ""),
                "title":     updated.get("summary", ""),
                "start":     updated.get("start", {}).get("dateTime", ""),
                "end":       updated.get("end",   {}).get("dateTime", ""),
            }, ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("UpdateEventTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "event_id":      {"type": "string",  "description": "Google Calendar event ID (required)"},
                "title":         {"type": "string",  "description": "New event title (optional)"},
                "start_time":    {"type": "string",  "description": "New start time ISO 8601 (optional)"},
                "duration_mins": {"type": "integer", "description": "New duration in minutes (optional)"},
                "description":   {"type": "string",  "description": "Updated description/agenda (optional)"},
                "location":      {"type": "string",  "description": "Updated location (optional)"},
                "add_attendees": {"type": "array", "items": {"type": "string"},
                                  "description": "Additional attendees to add (optional)"},
                "timezone":      {"type": "string",  "description": "IANA timezone (default: Asia/Kolkata)"},
            }, "required": ["event_id"]},
        }}
