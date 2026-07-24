"""
BlockFocusTimeTool — create a focus/DND block in Google Calendar.

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


class BlockFocusTimeTool(BaseTool):
    """Create a focus time / Do Not Disturb block in Google Calendar.

    Blocks appear as busy to other people, preventing them from
    scheduling over your focus time.

    Input::

        {
            "title":        "Focus Time",               # default: "Focus Time 🎯"
            "start_time":   "2026-07-09T10:00:00",
            "duration_mins": 120,                        # default: 60
            "date":         "2026-07-09",                # OR use date + work_start/work_end
            "work_start":   10,                          # 10am (for full-day blocking)
            "work_end":     13,                          # 1pm  (for full-day blocking)
            "frequency":    "daily",                     # optional: make it recurring
            "days_of_week": ["monday", "tuesday", "wednesday", "thursday", "friday"],
            "count":        10,                          # end after N occurrences
            "timezone":     "Asia/Kolkata"
        }

    Returns::

        {
            "status":    "blocked",
            "event_id":  "...",
            "event_url": "https://calendar.google.com/...",
            "title":     "Focus Time 🎯",
            "start":     "2026-07-09T10:00:00",
            "end":       "2026-07-09T12:00:00"
        }
    """

    name: str = "block_focus_time"
    description: str = (
        "Create a focus time / Do Not Disturb block in Google Calendar. "
        "REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"start_time\": \"2026-07-09T10:00:00\", \"duration_mins\": 120, "
        "\"title\": \"Deep Work\"} for a one-off block, or add "
        "\"frequency\": \"daily\", \"days_of_week\": [\"monday\",...] for recurring blocks."
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

        title         = data.get("title", "Focus Time 🎯")
        start_str     = data.get("start_time", "")
        duration_mins = int(data.get("duration_mins", 60))
        date_str      = data.get("date", "")
        work_start    = int(data.get("work_start", 9))
        work_end      = int(data.get("work_end", 11))
        frequency     = data.get("frequency", "")
        days_of_week  = data.get("days_of_week", [])
        count         = int(data.get("count", 0))
        until_str     = data.get("until", "")
        tz_name       = data.get("timezone", "Asia/Kolkata")

        # Resolve start time
        if start_str:
            try:
                start_dt = datetime.fromisoformat(start_str)
            except ValueError:
                return json.dumps({"error": f"Cannot parse start_time: '{start_str}'"})
        elif date_str:
            try:
                day      = datetime.fromisoformat(date_str)
                start_dt = day.replace(hour=work_start, minute=0, second=0)
                duration_mins = (work_end - work_start) * 60
            except ValueError:
                return json.dumps({"error": f"Cannot parse date: '{date_str}'"})
        else:
            return json.dumps({"error": "Either 'start_time' or 'date' is required."})

        end_dt = start_dt + timedelta(minutes=duration_mins)

        event_body: dict = {
            "summary":     title,
            "description": "Focus time — no interruptions please.",
            "start": {"dateTime": start_dt.isoformat(), "timeZone": tz_name},
            "end":   {"dateTime": end_dt.isoformat(),   "timeZone": tz_name},
            "colorId": "9",           # blueberry — visually distinct
            "visibility": "public",   # shows as "busy" to others
            "transparency": "opaque", # marks the time as busy
            "reminders": {"useDefault": False, "overrides": []},
        }

        # Recurring focus blocks
        if frequency:
            from core.tools.calendar.create_recurring_event import CreateRecurringEventTool
            _FREQ_MAP = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY"}
            _DAY_MAP  = {
                "monday": "MO", "tuesday": "TU", "wednesday": "WE",
                "thursday": "TH", "friday": "FR", "saturday": "SA", "sunday": "SU",
            }
            freq = _FREQ_MAP.get(frequency.lower(), "WEEKLY")
            rrule = f"RRULE:FREQ={freq}"
            if days_of_week and freq == "WEEKLY":
                byday = ",".join(_DAY_MAP.get(d.lower(), d.upper()[:2]) for d in days_of_week)
                rrule += f";BYDAY={byday}"
            if until_str:
                try:
                    until_dt = datetime.fromisoformat(until_str).replace(
                        hour=23, minute=59, second=59, tzinfo=timezone.utc
                    )
                    rrule += f";UNTIL={until_dt.strftime('%Y%m%dT%H%M%SZ')}"
                except ValueError:
                    pass
            elif count > 0:
                rrule += f";COUNT={count}"
            event_body["recurrence"] = [rrule]

        try:
            service = self._get_service()
            event   = service.events().insert(
                calendarId="primary",
                body=event_body,
            ).execute()

            logger.info("BlockFocusTimeTool: created id=%s title=%r", event.get("id"), title)
            return json.dumps({
                "status":    "blocked",
                "event_id":  event.get("id", ""),
                "event_url": event.get("htmlLink", ""),
                "title":     title,
                "start":     start_str or start_dt.isoformat(),
                "end":       end_dt.isoformat(),
                "duration_mins": duration_mins,
                "recurring": bool(frequency),
            }, ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("BlockFocusTimeTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "title":         {"type": "string"},
                "start_time":    {"type": "string", "description": "ISO 8601 start datetime"},
                "duration_mins": {"type": "integer"},
                "date":          {"type": "string", "description": "Date YYYY-MM-DD (use with work_start/work_end)"},
                "work_start":    {"type": "integer", "description": "Hour to start block (24h)"},
                "work_end":      {"type": "integer", "description": "Hour to end block (24h)"},
                "frequency":     {"type": "string", "enum": ["daily", "weekly", "monthly"],
                                  "description": "Make it recurring"},
                "days_of_week":  {"type": "array", "items": {"type": "string"}},
                "count":         {"type": "integer"},
                "until":         {"type": "string", "description": "End by date YYYY-MM-DD"},
                "timezone":      {"type": "string"},
            }},
        }}
