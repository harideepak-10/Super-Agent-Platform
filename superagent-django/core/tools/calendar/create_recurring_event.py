"""
CreateRecurringEventTool — create a repeating Google Calendar event.

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

_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]

_FREQ_MAP = {
    "daily":   "DAILY",
    "weekly":  "WEEKLY",
    "monthly": "MONTHLY",
    "yearly":  "YEARLY",
}

_DAY_MAP = {
    "monday": "MO", "tuesday": "TU", "wednesday": "WE",
    "thursday": "TH", "friday": "FR", "saturday": "SA", "sunday": "SU",
}


class CreateRecurringEventTool(BaseTool):
    """Create a repeating Google Calendar event.

    Input::

        {
            "title":        "Weekly Standup",
            "start_time":   "2026-07-13T09:00:00",   # first occurrence
            "duration_mins": 30,
            "frequency":    "weekly",                  # daily | weekly | monthly | yearly
            "interval":     1,                         # every N frequency units (default 1)
            "days_of_week": ["monday", "wednesday"],   # for weekly recurrence (optional)
            "count":        10,                        # end after N occurrences (optional)
            "until":        "2026-12-31",              # end by date (optional, overrides count)
            "attendees":    ["arun@example.com"],      # optional
            "description":  "Weekly team standup",    # optional
            "timezone":     "Asia/Kolkata"             # default: Asia/Kolkata
        }

    Returns::

        {
            "status":       "created",
            "event_id":     "...",
            "event_url":    "https://calendar.google.com/...",
            "title":        "Weekly Standup",
            "frequency":    "weekly",
            "first_occurrence": "2026-07-13T09:00:00",
            "recurrence_rule": "RRULE:FREQ=WEEKLY;BYDAY=MO"
        }
    """

    name: str = "create_recurring_event"
    description: str = (
        "Create a repeating Google Calendar event (daily, weekly, monthly). "
        "REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"title\": \"Weekly Standup\", \"start_time\": \"2026-07-13T09:00:00\", "
        "\"duration_mins\": 30, \"frequency\": \"weekly\", \"days_of_week\": [\"monday\", \"wednesday\"], "
        "\"count\": 10, \"attendees\": [\"email@...\"]}. "
        "Use current_time to resolve relative start dates."
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
            scopes=_CALENDAR_SCOPES,
        )
        return build("calendar", "v3", credentials=creds)

    def _build_rrule(self, frequency: str, interval: int, days_of_week: list,
                     count: int, until_str: str) -> str:
        freq = _FREQ_MAP.get(frequency.lower(), "WEEKLY")
        rule = f"RRULE:FREQ={freq}"
        if interval > 1:
            rule += f";INTERVAL={interval}"
        if days_of_week and freq == "WEEKLY":
            byday = ",".join(_DAY_MAP.get(d.lower(), d.upper()[:2]) for d in days_of_week)
            rule += f";BYDAY={byday}"
        if until_str:
            try:
                until_dt = datetime.fromisoformat(until_str).replace(
                    hour=23, minute=59, second=59, tzinfo=timezone.utc
                )
                rule += f";UNTIL={until_dt.strftime('%Y%m%dT%H%M%SZ')}"
            except ValueError:
                pass
        elif count and count > 0:
            rule += f";COUNT={count}"
        return rule

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else (input_str or {})
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        title         = data.get("title", "Recurring Meeting")
        start_str     = data.get("start_time", "")
        duration_mins = int(data.get("duration_mins", 60))
        frequency     = data.get("frequency", "weekly")
        interval      = int(data.get("interval", 1))
        days_of_week  = data.get("days_of_week", [])
        count         = int(data.get("count", 0))
        until_str     = data.get("until", "")
        attendees     = data.get("attendees", [])
        description   = data.get("description", "")
        tz_name       = data.get("timezone", "Asia/Kolkata")

        if not start_str:
            return json.dumps({"error": "'start_time' is required (ISO 8601)."})
        if frequency.lower() not in _FREQ_MAP:
            return json.dumps({"error": f"'frequency' must be one of: {list(_FREQ_MAP.keys())}"})

        try:
            start_dt = datetime.fromisoformat(start_str)
        except ValueError:
            return json.dumps({"error": f"Cannot parse start_time: '{start_str}'"})

        end_dt   = start_dt + timedelta(minutes=duration_mins)
        rrule    = self._build_rrule(frequency, interval, days_of_week, count, until_str)

        event_body = {
            "summary":     title,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": tz_name},
            "end":   {"dateTime": end_dt.isoformat(),   "timeZone": tz_name},
            "recurrence": [rrule],
            "attendees": [{"email": e.strip()} for e in attendees if e.strip()],
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email",  "minutes": 30},
                    {"method": "popup",  "minutes": 10},
                ],
            },
        }

        try:
            service = self._get_service()

            meet_link = ""
            try:
                event_body["conferenceData"] = {
                    "createRequest": {
                        "requestId": f"krypsos-rec-{int(datetime.now(timezone.utc).timestamp())}",
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
                event_body.pop("conferenceData", None)
                event = service.events().insert(
                    calendarId="primary",
                    body=event_body,
                    sendUpdates="all",
                ).execute()

            logger.info("CreateRecurringEventTool: created recurring event id=%s rrule=%s",
                        event.get("id"), rrule)
            return json.dumps({
                "status":           "created",
                "event_id":         event.get("id", ""),
                "event_url":        event.get("htmlLink", ""),
                "title":            title,
                "frequency":        frequency,
                "interval":         interval,
                "first_occurrence": start_str,
                "recurrence_rule":  rrule,
                "meet_link":        meet_link,
                "attendees":        attendees,
            }, ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("CreateRecurringEventTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "title":         {"type": "string"},
                "start_time":    {"type": "string", "description": "ISO 8601 first occurrence"},
                "duration_mins": {"type": "integer"},
                "frequency":     {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly"]},
                "interval":      {"type": "integer", "description": "Every N units (default 1)"},
                "days_of_week":  {"type": "array", "items": {"type": "string"},
                                  "description": "e.g. [\"monday\", \"wednesday\"] for weekly"},
                "count":         {"type": "integer", "description": "End after N occurrences"},
                "until":         {"type": "string", "description": "End by date YYYY-MM-DD"},
                "attendees":     {"type": "array", "items": {"type": "string"}},
                "description":   {"type": "string"},
                "timezone":      {"type": "string"},
            }, "required": ["title", "start_time", "frequency"]},
        }}
