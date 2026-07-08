"""
RespondToInviteTool — accept, decline, or tentatively accept a meeting invitation.

Zone: YELLOW — requires human approval before execution.
"""
from __future__ import annotations
import json
import logging
import os
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_VALID_RESPONSES = {"accepted", "declined", "tentative"}


class RespondToInviteTool(BaseTool):
    """Accept, decline, or tentatively accept a Google Calendar meeting invitation.

    Input::

        {
            "event_id": "abc123xyz",      # required (from list_events or get_event)
            "response": "accepted",        # required: "accepted", "declined", or "tentative"
            "comment":  "See you then!"   # optional — added to response email
        }

    Returns::

        {
            "status":   "responded",
            "event_id": "abc123xyz",
            "response": "accepted"
        }
    """

    name: str = "respond_to_invite"
    description: str = (
        "Accept, decline, or tentatively accept a Google Calendar meeting invitation. "
        "REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"event_id\": \"...\", \"response\": \"accepted\"} — "
        "response must be 'accepted', 'declined', or 'tentative'. "
        "Use list_events or get_event to find the event_id."
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
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        event_id = data.get("event_id", "")
        response = data.get("response", "").lower()
        comment  = data.get("comment", "")

        if not event_id:
            return json.dumps({"error": "'event_id' is required."})
        if response not in _VALID_RESPONSES:
            return json.dumps({"error": f"'response' must be one of: {sorted(_VALID_RESPONSES)}"})

        try:
            service = self._get_service()

            # Fetch event, find our attendee entry, update response
            event = service.events().get(calendarId="primary", eventId=event_id).execute()

            attendees = event.get("attendees", [])
            if not attendees:
                return json.dumps({"error": "No attendees found on this event — cannot RSVP."})

            # Mark our own entry (self=True or matching email)
            updated = False
            for attendee in attendees:
                if attendee.get("self", False):
                    attendee["responseStatus"] = response
                    if comment:
                        attendee["comment"] = comment
                    updated = True
                    break

            if not updated:
                # Fallback: update the first attendee (rare case)
                attendees[0]["responseStatus"] = response
                if comment:
                    attendees[0]["comment"] = comment

            event["attendees"] = attendees
            service.events().patch(
                calendarId="primary",
                eventId=event_id,
                body={"attendees": attendees},
                sendUpdates="all",
            ).execute()

            logger.info("RespondToInviteTool: event_id=%s response=%s", event_id, response)
            return json.dumps({
                "status":   "responded",
                "event_id": event_id,
                "response": response,
                "title":    event.get("summary", ""),
                "comment":  comment,
            })

        except Exception as exc:
            logger.exception("RespondToInviteTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "event_id": {"type": "string", "description": "Google Calendar event ID (required)"},
                "response": {"type": "string", "enum": ["accepted", "declined", "tentative"],
                             "description": "RSVP response"},
                "comment":  {"type": "string", "description": "Optional note sent with the response"},
            }, "required": ["event_id", "response"]},
        }}
