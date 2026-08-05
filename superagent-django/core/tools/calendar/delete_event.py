"""
DeleteEventTool — cancel/delete a Google Calendar event.

Zone: YELLOW — requires human approval before execution.
"""
from __future__ import annotations
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

# Real Google Calendar event IDs only ever use lowercase letters a-v and
# digits 0-9 (base32hex), 5-1024 chars. Anything else is a guessed/fabricated
# placeholder, not a real ID.
_VALID_EVENT_ID_RE = re.compile(r"^[a-v0-9]{5,1024}(_\d{8}T\d{6}Z)?$")


def _list_events_for_date(service: Any, date_str: str, tz_name: str = "Asia/Kolkata") -> list[dict]:
    """Fetch all events on a given calendar day (local tz)."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(tz_name)
    day_start = datetime.fromisoformat(date_str).replace(tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    result = service.events().list(
        calendarId="primary",
        timeMin=day_start.isoformat(),
        timeMax=day_end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return result.get("items", [])


def _resolve_event_id(
    service: Any,
    date_str: str,
    attendee_email: str = "",
    start_time: str = "",
    title_contains: str = "",
    tz_name: str = "Asia/Kolkata",
) -> tuple[str | None, dict | None]:
    """Deterministically find ONE matching event by date + optional filters.

    Returns (event_id, None) on a single match, or (None, error_payload) if
    zero or multiple events match — error_payload includes 'candidates' when
    there's more than one, so the caller can disambiguate.
    """
    if not date_str:
        return None, {"error": "A 'date' (YYYY-MM-DD) is required to look up the event automatically."}

    try:
        events = _list_events_for_date(service, date_str, tz_name)
    except Exception as exc:
        return None, {"error": f"Could not look up events for {date_str}: {exc}"}

    def matches(ev: dict) -> bool:
        if attendee_email:
            attendees = [a.get("email", "").lower() for a in ev.get("attendees", [])]
            if attendee_email.strip().lower() not in attendees:
                return False
        if title_contains:
            if title_contains.strip().lower() not in ev.get("summary", "").lower():
                return False
        if start_time:
            ev_start_str = ev.get("start", {}).get("dateTime", "")
            if not ev_start_str:
                return False
            try:
                ev_dt = datetime.fromisoformat(ev_start_str)
            except ValueError:
                return False
            st = start_time.strip()
            if "T" in st:
                try:
                    req_dt = datetime.fromisoformat(st)
                    if (req_dt.hour, req_dt.minute) != (ev_dt.hour, ev_dt.minute):
                        return False
                except ValueError:
                    pass
            else:
                try:
                    hh, mm = st.replace(".", ":").split(":")[:2]
                    if (int(hh), int(mm)) != (ev_dt.hour, ev_dt.minute):
                        return False
                except (ValueError, IndexError):
                    pass
        return True

    candidates = [ev for ev in events if matches(ev)]

    if len(candidates) == 0:
        return None, {"error": f"No event found on {date_str} matching the given details (attendee/time/title)."}

    if len(candidates) > 1:
        listing = [
            {
                "event_id": ev.get("id", ""),
                "title": ev.get("summary", ""),
                "start": ev.get("start", {}).get("dateTime", ""),
                "end": ev.get("end", {}).get("dateTime", ""),
                "attendees": [a.get("email", "") for a in ev.get("attendees", [])],
            }
            for ev in candidates
        ]
        return None, {
            "error": (
                f"Found {len(candidates)} events on {date_str} matching the given details. "
                "Ask the user which one they mean, then retry with that event's exact event_id."
            ),
            "candidates": listing,
        }

    return candidates[0].get("id", ""), None


class DeleteEventTool(BaseTool):
    """Cancel or delete a Google Calendar event.

    You can EITHER pass a real ``event_id`` (from a prior get_event/list_events
    call), OR let this tool find it for you by passing ``date`` plus at least
    one of ``attendee_email`` / ``start_time`` / ``title_contains``. If more
    than one event matches, the tool returns a list of candidates instead of
    deleting anything — nothing is ever deleted on an ambiguous match.

    Input::

        {
            "event_id":        "abc123xyz",   # optional if using lookup filters below
            "date":            "2026-07-30",  # optional — enables auto lookup
            "attendee_email":  "a@b.com",     # optional — lookup filter
            "start_time":      "16:00",       # optional — lookup filter (HH:MM or ISO)
            "title_contains":  "Standup",     # optional — lookup filter
            "notify":          true,          # send cancellation email to attendees (default: true)
            "reason":          "Meeting rescheduled"   # optional — added to cancellation note
        }

    Returns::

        {
            "status":   "deleted",
            "event_id": "abc123xyz",
            "notified": true
        }

    Or, if the lookup was ambiguous::

        {
            "error": "Found 2 events on 2026-07-30 matching the given details. ...",
            "candidates": [ {...}, {...} ]
        }
    """

    name: str = "delete_event"
    description: str = (
        "Cancel or delete a Google Calendar event. "
        "REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"event_id\": \"...\", \"notify\": true}. "
        "If you don't have a real event_id yet, you may instead pass "
        "{\"date\": \"YYYY-MM-DD\", \"attendee_email\": \"...\", \"start_time\": \"HH:MM\", "
        "\"title_contains\": \"...\"} (any combination) and this tool will look the event up "
        "and delete it if exactly one match is found. If multiple events match, it returns "
        "a list of candidates instead of deleting anything — ask the user to pick one. "
        "NEVER invent or guess an event_id."
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

    @staticmethod
    def _has_lookup_filters(data: dict) -> bool:
        return bool(data.get("date")) and bool(
            data.get("attendee_email") or data.get("start_time") or data.get("title_contains")
        )

    def validate(self, input_str: str) -> str | None:
        """Pre-approval, side-effect-free check. No network calls here — just
        confirms we have either a real-looking event_id, or enough info to
        look one up at run() time."""
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else (input_str or {})
        except (json.JSONDecodeError, TypeError):
            return "Invalid input."

        event_id = data.get("event_id", "")
        if event_id and _VALID_EVENT_ID_RE.match(event_id):
            return None  # real-looking ID, good to go

        if self._has_lookup_filters(data):
            return None  # no valid ID yet, but enough to look it up at run() time

        if event_id:
            return (
                f"'{event_id}' is not a real Google Calendar event ID — it looks like a "
                "guessed or made-up value. Either call list_events or get_event first to get "
                "the REAL event_id, or call delete_event with 'date' plus attendee_email/"
                "start_time/title_contains so it can look the event up automatically."
            )
        return (
            "'event_id' is required, OR provide 'date' plus at least one of "
            "attendee_email / start_time / title_contains so the event can be looked up "
            "automatically."
        )

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else (input_str or {})
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        event_id       = data.get("event_id", "")
        notify         = data.get("notify", True)
        reason         = data.get("reason", "")
        date_str       = data.get("date", "")
        attendee_email = data.get("attendee_email", "")
        start_time     = data.get("start_time", "")
        title_contains = data.get("title_contains", "")
        tz_name        = data.get("timezone", "Asia/Kolkata")

        has_valid_id = bool(event_id) and bool(_VALID_EVENT_ID_RE.match(event_id))

        if not has_valid_id and not self._has_lookup_filters(data):
            if event_id:
                return json.dumps({
                    "error": (
                        f"'{event_id}' is not a real Google Calendar event ID — it looks like a "
                        "guessed or made-up value. Call list_events or get_event first to get the "
                        "REAL event_id, or provide 'date' plus attendee_email/start_time/"
                        "title_contains so it can be looked up automatically."
                    )
                })
            return json.dumps({
                "error": (
                    "'event_id' is required, OR provide 'date' plus at least one of "
                    "attendee_email / start_time / title_contains."
                )
            })

        try:
            service = self._get_service()

            if not has_valid_id:
                resolved_id, err = _resolve_event_id(
                    service, date_str, attendee_email, start_time, title_contains, tz_name
                )
                if err:
                    return json.dumps(err)
                event_id = resolved_id

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
                "event_id":       {"type": "string",  "description": "Real Google Calendar event ID, if already known (optional if using lookup filters)"},
                "date":           {"type": "string",  "description": "YYYY-MM-DD — required if event_id is not known, enables auto-lookup"},
                "attendee_email": {"type": "string",  "description": "Lookup filter: an attendee's email"},
                "start_time":     {"type": "string",  "description": "Lookup filter: event start time, HH:MM or ISO"},
                "title_contains": {"type": "string",  "description": "Lookup filter: substring of the event title"},
                "notify":         {"type": "boolean", "description": "Send cancellation email to attendees (default: true)"},
                "reason":         {"type": "string",  "description": "Reason for cancellation (optional)"},
            }, "required": []},
        }}