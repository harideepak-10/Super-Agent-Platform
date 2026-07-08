"""
DetectConflictsTool — find overlapping events in Google Calendar.

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


class DetectConflictsTool(BaseTool):
    """Find overlapping/conflicting events in Google Calendar.

    Input::

        {
            "days_ahead": 7,          # how many days to scan (default 7)
            "date":       "2026-07-09" # OR check a specific date
        }

    Returns::

        {
            "conflicts_found": true,
            "conflicts": [
                {
                    "event_a": {"event_id": "...", "title": "Q3 Review",  "start": "...", "end": "..."},
                    "event_b": {"event_id": "...", "title": "Team Lunch", "start": "...", "end": "..."},
                    "overlap_start": "2026-07-09T11:30:00",
                    "overlap_end":   "2026-07-09T12:00:00",
                    "overlap_mins":  30
                }
            ],
            "total_conflicts": 1,
            "range": "2026-07-09 to 2026-07-16"
        }
    """

    name: str = "detect_conflicts"
    description: str = (
        "Find overlapping events in your Google Calendar. GREEN — runs automatically. "
        "Input JSON: {\"days_ahead\": 7} or {\"date\": \"2026-07-09\"}. "
        "Returns all pairs of events that overlap in time."
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

    def _parse_dt(self, dt_str: str) -> datetime | None:
        if not dt_str:
            return None
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            data = {}

        date_str   = data.get("date", "")
        days_ahead = int(data.get("days_ahead", 7))
        now        = datetime.now(timezone.utc)

        if date_str:
            try:
                day      = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                time_min = day.replace(hour=0, minute=0, second=0)
                time_max = day.replace(hour=23, minute=59, second=59)
            except ValueError:
                return json.dumps({"error": f"Invalid date: {date_str}"})
        else:
            time_min = now
            time_max = now + timedelta(days=days_ahead)

        try:
            service = self._get_service()
            result  = service.events().list(
                calendarId="primary",
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=100,
            ).execute()

            events_raw = result.get("items", [])

            # Build list of (event_id, title, start_dt, end_dt)
            events = []
            for e in events_raw:
                start_str = e.get("start", {}).get("dateTime", "")
                end_str   = e.get("end",   {}).get("dateTime", "")
                if not start_str or not end_str:
                    continue  # skip all-day events
                s = self._parse_dt(start_str)
                en = self._parse_dt(end_str)
                if s and en:
                    events.append({
                        "event_id": e.get("id", ""),
                        "title":    e.get("summary", "(no title)"),
                        "start":    start_str,
                        "end":      end_str,
                        "start_dt": s,
                        "end_dt":   en,
                    })

            # Find all overlapping pairs
            conflicts = []
            for i in range(len(events)):
                for j in range(i + 1, len(events)):
                    a = events[i]
                    b = events[j]
                    overlap_start = max(a["start_dt"], b["start_dt"])
                    overlap_end   = min(a["end_dt"],   b["end_dt"])
                    if overlap_start < overlap_end:
                        overlap_mins = int((overlap_end - overlap_start).total_seconds() / 60)
                        conflicts.append({
                            "event_a": {
                                "event_id": a["event_id"],
                                "title":    a["title"],
                                "start":    a["start"],
                                "end":      a["end"],
                            },
                            "event_b": {
                                "event_id": b["event_id"],
                                "title":    b["title"],
                                "start":    b["start"],
                                "end":      b["end"],
                            },
                            "overlap_start": overlap_start.isoformat(),
                            "overlap_end":   overlap_end.isoformat(),
                            "overlap_mins":  overlap_mins,
                        })

            date_range = f"{time_min.strftime('%Y-%m-%d')} to {time_max.strftime('%Y-%m-%d')}"
            logger.info("DetectConflictsTool: %d conflicts in %s", len(conflicts), date_range)
            return json.dumps({
                "conflicts_found":  len(conflicts) > 0,
                "conflicts":        conflicts,
                "total_conflicts":  len(conflicts),
                "events_scanned":   len(events),
                "range":            date_range,
                "note": (
                    "No conflicts found." if not conflicts
                    else f"{len(conflicts)} overlapping event(s) found. Use update_event or delete_event to resolve."
                ),
            }, ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("DetectConflictsTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "days_ahead": {"type": "integer", "description": "Days to scan ahead (default 7)"},
                "date":       {"type": "string",  "description": "Specific date YYYY-MM-DD"},
            }},
        }}
