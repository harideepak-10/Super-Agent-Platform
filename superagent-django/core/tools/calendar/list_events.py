"""
ListEventsTool — list upcoming Google Calendar events.

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
    """Convert a datetime string (UTC or any offset) to IST. All-day dates pass through."""
    if not dt_str or "T" not in dt_str:
        return dt_str  # all-day event — just a date string
    try:
        # Handle Z suffix
        s = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.astimezone(_IST).isoformat()
    except Exception:
        return dt_str

logger = logging.getLogger(__name__)


class ListEventsTool(BaseTool):
    """List upcoming events from Google Calendar.

    Input::

        {
            "days_ahead":   7,          # how many days to look ahead (default 7)
            "max_results":  20,         # max events to return (default 20)
            "date":         "2026-07-09" # specific date (optional — overrides days_ahead)
        }

    Returns::

        {
            "events": [
                {
                    "event_id":   "abc123",
                    "title":      "Q3 Review",
                    "start":      "2026-07-09T11:00:00+05:30",
                    "end":        "2026-07-09T12:00:00+05:30",
                    "attendees":  ["arun@example.com", "sankar@example.com"],
                    "location":   "Google Meet",
                    "meet_link":  "https://meet.google.com/...",
                    "status":     "confirmed",
                    "organizer":  "me@example.com"
                }
            ],
            "total": 3,
            "range": "2026-07-08 to 2026-07-15"
        }
    """

    name: str = "list_events"
    description: str = (
        "List upcoming Google Calendar events. "
        "Input JSON: {\"days_ahead\": 7, \"max_results\": 20} or {\"date\": \"2026-07-09\"} for a specific day. "
        "Returns list of events with title, time, attendees, and Meet link."
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
        import os
        from apps.integrations.models import Integration
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        integration = Integration.objects.filter(
            workspace_id=self._workspace_id,
            provider=Integration.Provider.GOOGLE_CALENDAR,
            status=Integration.Status.ACTIVE,
        ).first()
        if not integration or not integration.access_token:
            raise RuntimeError("Google Calendar not connected. Go to Integrations → Calendar → Connect.")
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
            data = {}

        max_results = int(data.get("max_results", 20))
        specific_date = data.get("date", "")

        now = datetime.now(timezone.utc)

        if specific_date:
            try:
                # Parse date and apply IST boundaries (midnight to 23:59 IST)
                day = datetime.fromisoformat(specific_date).date()
                from datetime import date as _date
                time_min = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=_IST)
                time_max = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=_IST)
            except ValueError:
                return json.dumps({"error": f"Invalid date format: {specific_date}. Use YYYY-MM-DD."})
        else:
            days_ahead = int(data.get("days_ahead", 7))
            # days_ahead=0 means "today" — use rest of today in IST (min 1 day window)
            if days_ahead <= 0:
                today_ist = now.astimezone(_IST)
                time_min = now
                time_max = datetime(
                    today_ist.year, today_ist.month, today_ist.day,
                    23, 59, 59, tzinfo=_IST
                )
            else:
                time_min = now
                time_max = now + timedelta(days=days_ahead)

        try:
            service = self._get_service()
            result  = service.events().list(
                calendarId="primary",
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            ).execute()

            events_raw = result.get("items", [])
            events = []
            for e in events_raw:
                start = e.get("start", {})
                end   = e.get("end",   {})
                start_str = _to_ist(start.get("dateTime", start.get("date", "")))
                end_str   = _to_ist(end.get("dateTime",   end.get("date", "")))

                attendees = [
                    a.get("email", "") for a in e.get("attendees", [])
                    if not a.get("self", False)
                ]

                meet_link = ""
                for ep in e.get("conferenceData", {}).get("entryPoints", []):
                    if ep.get("entryPointType") == "video":
                        meet_link = ep.get("uri", "")
                        break

                events.append({
                    "event_id":  e.get("id", ""),
                    "title":     e.get("summary", "(no title)"),
                    "start":     start_str,
                    "end":       end_str,
                    "attendees": attendees,
                    "location":  e.get("location", ""),
                    "meet_link": meet_link,
                    "status":    e.get("status", "confirmed"),
                    "organizer": e.get("organizer", {}).get("email", ""),
                    "description": e.get("description", ""),
                })

            date_range = f"{time_min.strftime('%Y-%m-%d')} to {time_max.strftime('%Y-%m-%d')}"
            logger.info("ListEventsTool: found %d events in %s", len(events), date_range)
            return json.dumps({"events": events, "total": len(events), "range": date_range},
                              ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("ListEventsTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "days_ahead":  {"type": "integer", "description": "Number of days to look ahead (default 7)"},
                "max_results": {"type": "integer", "description": "Max events to return (default 20)"},
                "date":        {"type": "string",  "description": "Specific date YYYY-MM-DD (shows events for that day only)"},
            }},
        }}
