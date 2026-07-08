"""
SuggestMeetingTimeTool — find the best available slot for all attendees.

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


class SuggestMeetingTimeTool(BaseTool):
    """Find the best time slot that works for all attendees.

    Queries freebusy for organizer + all attendees across a date range,
    then returns ranked slots where everyone is free.

    Input::

        {
            "attendees":    ["arun@example.com", "sankar@example.com"],
            "duration_mins": 60,
            "days_ahead":   3,              # look across next N days (default 3)
            "work_start":   9,              # work hours start (default 9)
            "work_end":     18,             # work hours end (default 18)
            "top_n":        3,              # return top N suggestions (default 3)
            "timezone":     "Asia/Kolkata"
        }

    Returns::

        {
            "suggestions": [
                {
                    "rank":   1,
                    "date":   "2026-07-09",
                    "start":  "2026-07-09T14:00:00",
                    "end":    "2026-07-09T15:00:00",
                    "label":  "Wednesday, 9 Jul — 2:00 PM to 3:00 PM",
                    "all_free": true
                }
            ],
            "attendees_checked": ["arun@example.com", "sankar@example.com"],
            "duration_mins": 60
        }
    """

    name: str = "suggest_meeting_time"
    description: str = (
        "Find the best available time slot that works for all attendees. GREEN — auto. "
        "Input JSON: {\"attendees\": [\"email@...\"], \"duration_mins\": 60, \"days_ahead\": 3}. "
        "Returns top ranked slots where everyone is free. "
        "Follow up with create_meeting to book the chosen slot."
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
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        attendees     = data.get("attendees", [])
        duration_mins = int(data.get("duration_mins", 60))
        days_ahead    = int(data.get("days_ahead", 3))
        work_start    = int(data.get("work_start", 9))
        work_end      = int(data.get("work_end", 18))
        top_n         = int(data.get("top_n", 3))
        tz_name       = data.get("timezone", "Asia/Kolkata")

        if not attendees:
            return json.dumps({"error": "'attendees' list is required."})

        now      = datetime.now(timezone.utc)
        slot_dur = timedelta(minutes=duration_mins)

        # Build items list: self + all attendees
        items = [{"id": "primary"}] + [{"id": e.strip()} for e in attendees if e.strip()]

        suggestions = []

        try:
            service = self._get_service()

            for day_offset in range(days_ahead):
                day      = now + timedelta(days=day_offset)
                day_start= day.replace(hour=work_start, minute=0, second=0, microsecond=0)
                day_end  = day.replace(hour=work_end,   minute=0, second=0, microsecond=0)

                # Get freebusy for all calendars in one call
                fb_result = service.freebusy().query(body={
                    "timeMin": day_start.isoformat(),
                    "timeMax": day_end.isoformat(),
                    "items":   items,
                }).execute()

                calendars = fb_result.get("calendars", {})

                # Merge all busy periods across all attendees + self
                all_busy = []
                for item in items:
                    cal_id   = item["id"]
                    busy_key = "primary" if cal_id == "primary" else cal_id
                    for bp in calendars.get(busy_key, {}).get("busy", []):
                        bp_start = datetime.fromisoformat(bp["start"].replace("Z", "+00:00"))
                        bp_end   = datetime.fromisoformat(bp["end"].replace("Z", "+00:00"))
                        all_busy.append((bp_start, bp_end))

                # Sort and merge overlapping busy periods
                all_busy.sort(key=lambda x: x[0])
                merged_busy = []
                for bp_start, bp_end in all_busy:
                    if merged_busy and bp_start <= merged_busy[-1][1]:
                        merged_busy[-1] = (merged_busy[-1][0], max(merged_busy[-1][1], bp_end))
                    else:
                        merged_busy.append((bp_start, bp_end))

                # Walk through work hours finding free slots
                current = day_start
                for bp_start, bp_end in merged_busy:
                    while current + slot_dur <= bp_start and len(suggestions) < top_n * 3:
                        slot_end = current + slot_dur
                        suggestions.append({
                            "date":  day.strftime("%Y-%m-%d"),
                            "start": current.strftime("%Y-%m-%dT%H:%M:%S"),
                            "end":   slot_end.strftime("%Y-%m-%dT%H:%M:%S"),
                            "start_dt": current,
                        })
                        current = slot_end
                    current = max(current, bp_end)

                while current + slot_dur <= day_end and len(suggestions) < top_n * 3:
                    slot_end = current + slot_dur
                    suggestions.append({
                        "date":  day.strftime("%Y-%m-%d"),
                        "start": current.strftime("%Y-%m-%dT%H:%M:%S"),
                        "end":   slot_end.strftime("%Y-%m-%dT%H:%M:%S"),
                        "start_dt": current,
                    })
                    current = slot_end

                if len(suggestions) >= top_n:
                    break

            # Rank and format output — prefer morning slots, then afternoon
            def slot_score(s: dict) -> int:
                hour = s["start_dt"].hour
                # Prefer 9-11am (score 0), then 2-4pm (score 1), then other
                if 9 <= hour < 11:
                    return 0
                if 14 <= hour < 16:
                    return 1
                return 2

            suggestions.sort(key=slot_score)
            top_suggestions = suggestions[:top_n]

            output = []
            for i, s in enumerate(top_suggestions):
                start_dt = s["start_dt"]
                end_dt   = start_dt + slot_dur
                label    = (
                    f"{start_dt.strftime('%A, %-d %b')} — "
                    f"{start_dt.strftime('%I:%M %p')} to {end_dt.strftime('%I:%M %p')}"
                )
                output.append({
                    "rank":     i + 1,
                    "date":     s["date"],
                    "start":    s["start"],
                    "end":      s["end"],
                    "label":    label,
                    "all_free": True,
                })

            logger.info("SuggestMeetingTimeTool: %d suggestions for attendees=%s", len(output), attendees)
            return json.dumps({
                "suggestions":        output,
                "attendees_checked":  attendees,
                "duration_mins":      duration_mins,
                "note": (
                    f"Top {len(output)} slot(s) where everyone is free. "
                    "Use create_meeting with the chosen start time to book."
                ) if output else "No free slots found in the given range.",
            }, ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("SuggestMeetingTimeTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "attendees":     {"type": "array", "items": {"type": "string"},
                                  "description": "Attendee emails"},
                "duration_mins": {"type": "integer", "description": "Meeting duration (default 60)"},
                "days_ahead":    {"type": "integer", "description": "Days to search ahead (default 3)"},
                "work_start":    {"type": "integer", "description": "Work hours start 24h (default 9)"},
                "work_end":      {"type": "integer", "description": "Work hours end 24h (default 18)"},
                "top_n":         {"type": "integer", "description": "Number of suggestions (default 3)"},
                "timezone":      {"type": "string"},
            }, "required": ["attendees"]},
        }}
