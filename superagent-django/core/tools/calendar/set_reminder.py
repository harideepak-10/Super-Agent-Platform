"""
SetReminderTool — add/update reminders on an existing event, or create a standalone reminder event.

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


class SetReminderTool(BaseTool):
    """Add reminders to an existing Calendar event, or create a standalone reminder event.

    --- Update reminders on existing event ---
    Input::

        {
            "event_id": "abc123xyz",
            "reminders": [
                {"method": "email",  "minutes": 60},
                {"method": "popup",  "minutes": 15}
            ]
        }

    --- Create a standalone reminder event ---
    Input::

        {
            "title":        "Follow up with Arun",
            "remind_at":    "2026-07-10T09:00:00",     # when to fire the reminder
            "description":  "Check invoice status",     # optional
            "timezone":     "Asia/Kolkata",             # default: Asia/Kolkata
            "reminders": [
                {"method": "popup",  "minutes": 0},     # popup at the event time
                {"method": "email",  "minutes": 0}
            ]
        }

    Returns::

        {
            "status":   "reminders_set",        # or "reminder_created"
            "event_id": "...",
            "reminders": [{"method": "popup", "minutes": 15}]
        }
    """

    name: str = "set_reminder"
    description: str = (
        "Add or update reminders on a Google Calendar event, or create a standalone reminder. "
        "GREEN — runs automatically. "
        "To update existing event: {\"event_id\": \"...\", \"reminders\": [{\"method\": \"popup\", \"minutes\": 15}]}. "
        "To create standalone reminder: {\"title\": \"...\", \"remind_at\": \"2026-07-10T09:00:00\", "
        "\"reminders\": [{\"method\": \"popup\", \"minutes\": 0}]}."
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

    def _build_reminders(self, reminders_input: list) -> dict:
        """Build Google Calendar reminders object from input list."""
        overrides = []
        for r in reminders_input:
            method  = r.get("method", "popup")
            minutes = int(r.get("minutes", 10))
            if method not in ("email", "popup", "sms"):
                method = "popup"
            overrides.append({"method": method, "minutes": minutes})
        return {"useDefault": False, "overrides": overrides}

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else (input_str or {})
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        event_id      = data.get("event_id", "")
        reminders_in  = data.get("reminders", [{"method": "popup", "minutes": 15}])
        title         = data.get("title", "")
        remind_at_str = data.get("remind_at", "")
        description   = data.get("description", "")
        tz_name       = data.get("timezone", "Asia/Kolkata")

        try:
            service = self._get_service()

            # --- Mode 1: Update reminders on existing event ---
            if event_id:
                event = service.events().get(calendarId="primary", eventId=event_id).execute()
                event["reminders"] = self._build_reminders(reminders_in)
                updated = service.events().update(
                    calendarId="primary",
                    eventId=event_id,
                    body=event,
                ).execute()
                logger.info("SetReminderTool: reminders updated event_id=%s", event_id)
                return json.dumps({
                    "status":    "reminders_set",
                    "event_id":  event_id,
                    "title":     updated.get("summary", ""),
                    "reminders": reminders_in,
                })

            # --- Mode 2: Create standalone reminder event ---
            if not title:
                return json.dumps({"error": "Either 'event_id' (to update) or 'title' + 'remind_at' (to create reminder) is required."})

            if remind_at_str:
                try:
                    remind_dt = datetime.fromisoformat(remind_at_str)
                except ValueError:
                    return json.dumps({"error": f"Cannot parse remind_at: '{remind_at_str}'. Use ISO 8601."})
            else:
                # Default: remind in 1 hour
                remind_dt = datetime.now(timezone.utc) + timedelta(hours=1)

            # 15-minute duration for the reminder event
            end_dt = remind_dt + timedelta(minutes=15)

            event_body = {
                "summary":     title,
                "description": description,
                "start": {"dateTime": remind_dt.isoformat(), "timeZone": tz_name},
                "end":   {"dateTime": end_dt.isoformat(),   "timeZone": tz_name},
                "reminders": self._build_reminders(reminders_in),
                "colorId": "5",  # banana yellow — visually distinct from regular meetings
            }

            created = service.events().insert(
                calendarId="primary",
                body=event_body,
            ).execute()

            logger.info("SetReminderTool: reminder event created id=%s", created.get("id"))
            return json.dumps({
                "status":    "reminder_created",
                "event_id":  created.get("id", ""),
                "event_url": created.get("htmlLink", ""),
                "title":     title,
                "remind_at": remind_at_str,
                "reminders": reminders_in,
            }, ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("SetReminderTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "event_id":   {"type": "string", "description": "Existing event ID to update reminders on"},
                "title":      {"type": "string", "description": "Title for new standalone reminder event"},
                "remind_at":  {"type": "string", "description": "When to fire reminder ISO 8601 (for new reminder)"},
                "description":{"type": "string", "description": "Description for reminder event (optional)"},
                "timezone":   {"type": "string", "description": "IANA timezone (default: Asia/Kolkata)"},
                "reminders":  {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "method":  {"type": "string", "enum": ["popup", "email"], "description": "Notification method"},
                            "minutes": {"type": "integer", "description": "Minutes before event to notify"},
                        },
                    },
                    "description": "List of reminders e.g. [{\"method\": \"popup\", \"minutes\": 15}]",
                },
            }},
        }}
