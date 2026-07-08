"""
CheckAttendeeAvailabilityTool — check freebusy for multiple attendees.

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


class CheckAttendeeAvailabilityTool(BaseTool):
    """Check if all attendees are free for a proposed meeting time.

    Uses Google Calendar's freebusy API to query each attendee's calendar
    (only works if they share availability with you).

    Input::

        {
            "attendees":    ["arun@example.com", "sankar@example.com"],
            "start_time":   "2026-07-09T11:00:00",
            "duration_mins": 60,
            "timezone":     "Asia/Kolkata"
        }

    Returns::

        {
            "all_free":   false,
            "proposed":   {"start": "2026-07-09T11:00:00", "end": "2026-07-09T12:00:00"},
            "attendees":  [
                {"email": "arun@example.com",   "free": true,  "busy_during": []},
                {"email": "sankar@example.com", "free": false, "busy_during": [
                    {"start": "2026-07-09T11:30:00", "end": "2026-07-09T12:00:00"}
                ]}
            ],
            "conflicts": ["sankar@example.com"]
        }
    """

    name: str = "check_attendee_availability"
    description: str = (
        "Check if all attendees are free for a proposed meeting time using Google freebusy API. "
        "GREEN — runs automatically. "
        "Input JSON: {\"attendees\": [\"email1@...\", \"email2@...\"], "
        "\"start_time\": \"2026-07-09T11:00:00\", \"duration_mins\": 60}. "
        "Returns who is free and who has conflicts."
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
        start_str     = data.get("start_time", "")
        duration_mins = int(data.get("duration_mins", 60))
        tz_name       = data.get("timezone", "Asia/Kolkata")

        if not attendees:
            return json.dumps({"error": "'attendees' list is required."})
        if not start_str:
            return json.dumps({"error": "'start_time' is required."})

        try:
            start_dt = datetime.fromisoformat(start_str)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return json.dumps({"error": f"Cannot parse start_time: '{start_str}'"})

        end_dt = start_dt + timedelta(minutes=duration_mins)

        # Always include self (primary) + all attendees
        items = [{"id": "primary"}] + [{"id": email.strip()} for email in attendees if email.strip()]

        try:
            service = self._get_service()
            result  = service.freebusy().query(body={
                "timeMin": start_dt.isoformat(),
                "timeMax": end_dt.isoformat(),
                "items":   items,
            }).execute()

            calendars = result.get("calendars", {})
            attendee_results = []
            conflicts = []

            for email in attendees:
                email = email.strip()
                if not email:
                    continue
                busy_periods = calendars.get(email, {}).get("busy", [])
                # Check if any busy period overlaps the proposed slot
                is_free = len(busy_periods) == 0
                if not is_free:
                    conflicts.append(email)
                attendee_results.append({
                    "email":        email,
                    "free":         is_free,
                    "busy_during":  busy_periods,
                    "note":         "" if is_free else "Has conflicting event during this time",
                })

            # Also check self
            self_busy = calendars.get("primary", {}).get("busy", [])
            organizer_free = len(self_busy) == 0

            logger.info("CheckAttendeeAvailabilityTool: all_free=%s conflicts=%s",
                        len(conflicts) == 0 and organizer_free, conflicts)
            return json.dumps({
                "all_free":       len(conflicts) == 0 and organizer_free,
                "organizer_free": organizer_free,
                "organizer_busy": self_busy,
                "proposed": {
                    "start":         start_str,
                    "end":           end_dt.isoformat(),
                    "duration_mins": duration_mins,
                },
                "attendees":  attendee_results,
                "conflicts":  conflicts,
                "note": (
                    "All attendees are free during this time." if not conflicts and organizer_free
                    else f"Conflicts found for: {', '.join(conflicts) if conflicts else 'organizer'}. "
                         "Use find_free_slots or suggest_meeting_time to find a better slot."
                ),
            }, ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("CheckAttendeeAvailabilityTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "attendees":     {"type": "array", "items": {"type": "string"},
                                  "description": "List of attendee emails"},
                "start_time":    {"type": "string", "description": "Proposed start time ISO 8601"},
                "duration_mins": {"type": "integer", "description": "Meeting duration in minutes"},
                "timezone":      {"type": "string"},
            }, "required": ["attendees", "start_time"]},
        }}
