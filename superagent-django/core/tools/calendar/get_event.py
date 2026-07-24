"""
GetEventTool — get full details of a specific Calendar event.

Zone: GREEN — runs automatically, no human approval required.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

_IST = timezone(timedelta(hours=5, minutes=30))


def _to_ist(dt_str: str) -> str:
    if not dt_str or "T" not in dt_str:
        return dt_str
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(_IST).isoformat()
    except Exception:
        return dt_str

logger = logging.getLogger(__name__)


class GetEventTool(BaseTool):
    """Get full details of a specific Google Calendar event.

    Input (one of)::

        {"event_id": "abc123xyz"}                    # by event ID (most precise)
        {"title": "Q3 Review"}                       # by title (searches upcoming events)
        {"title": "Q3 Review", "date": "2026-07-09"} # by title on specific date

    Returns::

        {
            "event_id":    "abc123xyz",
            "title":       "Q3 Review",
            "start":       "2026-07-09T11:00:00+05:30",
            "end":         "2026-07-09T12:00:00+05:30",
            "attendees":   [{"email": "arun@example.com", "status": "accepted"}],
            "location":    "Google Meet",
            "meet_link":   "https://meet.google.com/...",
            "description": "Quarterly review meeting",
            "status":      "confirmed",
            "organizer":   "me@example.com",
            "reminders":   [{"method": "email", "minutes": 30}]
        }
    """

    name: str = "get_event"
    description: str = (
        "Get full details of a specific Calendar event. "
        "Input JSON: {\"event_id\": \"...\"} if you have the ID, "
        "or {\"title\": \"Q3 Review\"} to search by title, "
        "or {\"title\": \"...\", \"date\": \"2026-07-09\"} for a specific date. "
        "Returns full event details including attendees, Meet link, and reminders."
    )
    zone: ToolZone = ToolZone.GREEN

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

    def _format_event(self, e: dict) -> dict:
        start = e.get("start", {})
        end   = e.get("end",   {})
        meet_link = ""
        for ep in e.get("conferenceData", {}).get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                meet_link = ep.get("uri", "")
                break
        reminders = e.get("reminders", {}).get("overrides", [])
        if not reminders and e.get("reminders", {}).get("useDefault"):
            reminders = [{"method": "popup", "minutes": 30}]
        return {
            "event_id":    e.get("id", ""),
            "title":       e.get("summary", "(no title)"),
            "start":       _to_ist(start.get("dateTime", start.get("date", ""))),
            "end":         _to_ist(end.get("dateTime",   end.get("date", ""))),
            "attendees":   [
                {"email": a.get("email", ""), "status": a.get("responseStatus", "needsAction")}
                for a in e.get("attendees", [])
            ],
            "location":    e.get("location", ""),
            "meet_link":   meet_link,
            "description": e.get("description", ""),
            "status":      e.get("status", "confirmed"),
            "organizer":   e.get("organizer", {}).get("email", ""),
            "reminders":   reminders,
            "event_url":   e.get("htmlLink", ""),
        }

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else (input_str or {})
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        event_id = data.get("event_id", "")
        title    = data.get("title", "")
        date_str = data.get("date", "")

        if not event_id and not title:
            return json.dumps({"error": "Either 'event_id' or 'title' is required."})

        try:
            service = self._get_service()

            if event_id:
                event = service.events().get(calendarId="primary", eventId=event_id).execute()
                return json.dumps({"found": True, "event": self._format_event(event)},
                                  ensure_ascii=False, default=str)

            # Search by title
            now = datetime.now(timezone.utc)
            if date_str:
                try:
                    day = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                    time_min = day.replace(hour=0, minute=0, second=0)
                    time_max = day.replace(hour=23, minute=59, second=59)
                except ValueError:
                    time_min = now
                    time_max = now + timedelta(days=30)
            else:
                time_min = now
                time_max = now + timedelta(days=30)

            result = service.events().list(
                calendarId="primary",
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                q=title,
                singleEvents=True,
                orderBy="startTime",
                maxResults=5,
            ).execute()

            items = result.get("items", [])
            if not items:
                return json.dumps({"found": False, "event": None, "message": f"No event found with title '{title}'"})

            return json.dumps({
                "found":   True,
                "event":   self._format_event(items[0]),
                "matches": len(items),
            }, ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("GetEventTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "event_id": {"type": "string", "description": "Google Calendar event ID"},
                "title":    {"type": "string", "description": "Event title to search for"},
                "date":     {"type": "string", "description": "Date YYYY-MM-DD to narrow search"},
            }},
        }}
