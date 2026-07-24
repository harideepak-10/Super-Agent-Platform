"""
FindFreeSlotsTool — find available time slots in Google Calendar.

Zone: GREEN — runs automatically, no human approval required.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_DEFAULT_WORK_START = 9   # 9 AM
_DEFAULT_WORK_END   = 18  # 6 PM
_DEFAULT_SLOT_MINS  = 60  # 1 hour slots


class FindFreeSlotsTool(BaseTool):
    """Find free time slots in Google Calendar for a given date range.

    Input::

        {
            "date":         "2026-07-09",   # specific date to check
            "days_ahead":   3,              # OR check next N days (default 1)
            "duration_mins": 60,            # meeting duration needed (default 60)
            "work_start":   9,              # work hours start (default 9am)
            "work_end":     18,             # work hours end (default 6pm)
            "timezone":     "Asia/Kolkata"  # default: Asia/Kolkata
        }

    Returns::

        {
            "free_slots": [
                {
                    "date":  "2026-07-09",
                    "start": "2026-07-09T09:00:00",
                    "end":   "2026-07-09T10:00:00",
                    "label": "9:00 AM – 10:00 AM"
                }
            ],
            "busy_slots": [
                {"start": "2026-07-09T11:00:00", "end": "2026-07-09T12:00:00", "title": "Q3 Review"}
            ],
            "total_free": 4
        }
    """

    name: str = "find_free_slots"
    description: str = (
        "Find available time slots in Google Calendar. "
        "Input JSON: {\"date\": \"2026-07-09\", \"duration_mins\": 60} for a specific day, "
        "or {\"days_ahead\": 3, \"duration_mins\": 60} for next N days. "
        "Returns free slots during working hours."
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

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else (input_str or {})
        except (json.JSONDecodeError, TypeError):
            data = {}

        duration_mins = int(data.get("duration_mins", _DEFAULT_SLOT_MINS))
        work_start    = int(data.get("work_start", _DEFAULT_WORK_START))
        work_end      = int(data.get("work_end",   _DEFAULT_WORK_END))
        date_str      = data.get("date", "")
        days_ahead    = int(data.get("days_ahead", 1))

        now = datetime.now(timezone.utc)

        if date_str:
            try:
                base = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
            except ValueError:
                return json.dumps({"error": f"Invalid date: {date_str}"})
            dates = [base]
        else:
            dates = [now + timedelta(days=i) for i in range(days_ahead)]

        try:
            service = self._get_service()
            all_free  = []
            all_busy  = []

            for day in dates:
                day_start = day.replace(hour=work_start, minute=0, second=0, microsecond=0)
                day_end   = day.replace(hour=work_end,   minute=0, second=0, microsecond=0)

                # Get busy periods from freebusy API
                freebusy_result = service.freebusy().query(body={
                    "timeMin": day_start.isoformat(),
                    "timeMax": day_end.isoformat(),
                    "items":   [{"id": "primary"}],
                }).execute()

                busy_periods = freebusy_result.get("calendars", {}).get("primary", {}).get("busy", [])

                # Get event titles for busy periods
                events_result = service.events().list(
                    calendarId="primary",
                    timeMin=day_start.isoformat(),
                    timeMax=day_end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                ).execute()
                event_map = {}
                for e in events_result.get("items", []):
                    s = e.get("start", {}).get("dateTime", "")
                    if s:
                        event_map[s] = e.get("summary", "Busy")

                for bp in busy_periods:
                    all_busy.append({
                        "date":  day.strftime("%Y-%m-%d"),
                        "start": bp["start"],
                        "end":   bp["end"],
                        "title": event_map.get(bp["start"], "Busy"),
                    })

                # Find free slots
                busy_sorted = sorted(busy_periods, key=lambda x: x["start"])
                current = day_start
                slot_delta = timedelta(minutes=duration_mins)

                for bp in busy_sorted:
                    bp_start = datetime.fromisoformat(bp["start"].replace("Z", "+00:00"))
                    bp_end   = datetime.fromisoformat(bp["end"].replace("Z", "+00:00"))
                    while current + slot_delta <= bp_start:
                        slot_end = current + slot_delta
                        all_free.append({
                            "date":  day.strftime("%Y-%m-%d"),
                            "start": current.strftime("%Y-%m-%dT%H:%M:%S"),
                            "end":   slot_end.strftime("%Y-%m-%dT%H:%M:%S"),
                            "label": f"{current.strftime('%I:%M %p')} – {slot_end.strftime('%I:%M %p')}",
                        })
                        current = slot_end
                    current = max(current, bp_end)

                while current + slot_delta <= day_end:
                    slot_end = current + slot_delta
                    all_free.append({
                        "date":  day.strftime("%Y-%m-%d"),
                        "start": current.strftime("%Y-%m-%dT%H:%M:%S"),
                        "end":   slot_end.strftime("%Y-%m-%dT%H:%M:%S"),
                        "label": f"{current.strftime('%I:%M %p')} – {slot_end.strftime('%I:%M %p')}",
                    })
                    current = slot_end

            logger.info("FindFreeSlotsTool: %d free slots found", len(all_free))
            return json.dumps({
                "free_slots": all_free,
                "busy_slots": all_busy,
                "total_free": len(all_free),
                "duration_checked_mins": duration_mins,
            }, ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("FindFreeSlotsTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "date":          {"type": "string",  "description": "Specific date YYYY-MM-DD"},
                "days_ahead":    {"type": "integer", "description": "Check next N days (default 1)"},
                "duration_mins": {"type": "integer", "description": "Meeting duration needed in minutes (default 60)"},
                "work_start":    {"type": "integer", "description": "Work hours start (24h, default 9)"},
                "work_end":      {"type": "integer", "description": "Work hours end (24h, default 18)"},
            }},
        }}
