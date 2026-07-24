"""
DeleteEventTool — cancel/delete a Google Calendar event.

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


class DeleteEventTool(BaseTool):
    """Cancel or delete a Google Calendar event.

    Input::

        {
            "event_id":   "abc123xyz",            # required
            "notify":     true,                    # send cancellation email to attendees (default: true)
            "reason":     "Meeting rescheduled"   # optional — added to cancellation note
        }

    Returns::

        {
            "status":   "deleted",
            "event_id": "abc123xyz",
            "notified": true
        }
    """

    name: str = "delete_event"
    description: str = (
        "Cancel or delete a Google Calendar event. "
        "REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"event_id\": \"...\", \"notify\": true}. "
        "Sends cancellation emails to all attendees by default. "
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

        event_id = data.get("event_id", "")
        notify   = data.get("notify", True)
        reason   = data.get("reason", "")

        if not event_id:
            return json.dumps({"error": "'event_id' is required. Use get_event or list_events to find it."})

        try:
            service = self._get_service()

            # If there's a reason, add it to description before deleting
            if reason:
                try:
                    event = service.events().get(calendarId="primary", eventId=event_id).execute()
                    existing_desc = event.get("description", "")
                    cancellation_note = f"[Cancelled: {reason}]"
                    event["description"] = f"{cancellation_note}\n\n{existing_desc}".strip()
                    event["status"] = "cancelled"
                    service.events().update(
                        calendarId="primary",
                        eventId=event_id,
                        body=event,
                        sendUpdates="all" if notify else "none",
                    ).execute()
                except Exception:
                    pass  # If update fails, still try to delete

            send_updates = "all" if notify else "none"
            service.events().delete(
                calendarId="primary",
                eventId=event_id,
                sendUpdates=send_updates,
            ).execute()

            logger.info("DeleteEventTool: event deleted id=%s notify=%s", event_id, notify)
            return json.dumps({
                "status":   "deleted",
                "event_id": event_id,
                "notified": notify,
                "reason":   reason,
            })

        except Exception as exc:
            logger.exception("DeleteEventTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "event_id": {"type": "string",  "description": "Google Calendar event ID (required)"},
                "notify":   {"type": "boolean", "description": "Send cancellation email to attendees (default: true)"},
                "reason":   {"type": "string",  "description": "Reason for cancellation (optional)"},
            }, "required": ["event_id"]},
        }}
