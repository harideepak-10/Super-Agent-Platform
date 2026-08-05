"""
UpdateEventTool — update/reschedule an existing Google Calendar event.

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

    NOTE: when using update_event to RESCHEDULE, pass the event's CURRENT
    start_time here (not the new one you want to move it to) — this filter
    is for finding the event, not for setting its new time.

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


class UpdateEventTool(BaseTool):
    """Update or reschedule an existing Google Calendar event.

    You can EITHER pass a real ``event_id`` (from a prior get_event/list_events
    call), OR let this tool find it for you by passing ``lookup_date`` plus at
    least one of ``attendee_email`` / ``current_start_time`` / ``title_contains``.
    If more than one event matches, the tool returns a list of candidates
    instead of updating anything.

    Input::

        {
            "event_id":           "abc123xyz",     # optional if using lookup filters below
            "lookup_date":        "2026-07-30",     # optional — enables auto lookup, event's CURRENT date
            "attendee_email":     "a@b.com",        # optional — lookup filter
            "current_start_time": "17:00",          # optional — lookup filter, event's CURRENT start (HH:MM or ISO)
            "title_contains":     "Standup",        # optional — lookup filter

            "title":         "New title",           # optional — update title
            "start_time":    "2026-07-10T14:00:00", # optional — NEW start time to reschedule to
            "duration_mins": 90,                    # optional — change duration
            "description":   "Updated agenda",      # optional
            "location":      "Office",              # optional
            "add_attendees": ["new@example.com"],   # optional — add new attendees
            "timezone":      "Asia/Kolkata"         # default: Asia/Kolkata
        }

    Returns::

        {
            "status":    "updated",
            "event_id":  "abc123xyz",
            "event_url": "https://calendar.google.com/...",
            "title":     "New title",
            "start":     "2026-07-10T14:00:00+05:30",
            "end":       "2026-07-10T15:30:00+05:30"
        }

    Or, if the lookup was ambiguous::

        {
            "error": "Found 2 events on 2026-07-30 matching the given details. ...",
            "candidates": [ {...}, {...} ]
        }
    """

    name: str = "update_event"
    description: str = (
        "Update or reschedule an existing Google Calendar event. "
        "REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"event_id\": \"...\", \"start_time\": \"2026-07-10T14:00:00\", "
        "\"duration_mins\": 60, \"title\": \"New title (optional)\"}. "
        "If you don't have a real event_id yet, you may instead pass "
        "{\"lookup_date\": \"YYYY-MM-DD\", \"attendee_email\": \"...\", "
        "\"current_start_time\": \"HH:MM\", \"title_contains\": \"...\"} (any combination, "
        "using the event's CURRENT date/time — not the new one) and this tool will look the "
        "event up and update it if exactly one match is found. If multiple events match, it "
        "returns a list of candidates instead of updating anything — ask the user to pick one. "
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
        return bool(data.get("lookup_date")) and bool(
            data.get("attendee_email") or data.get("current_start_time") or data.get("title_contains")
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
                "the REAL event_id, or call update_event with 'lookup_date' plus "
                "attendee_email/current_start_time/title_contains so it can look the event up "
                "automatically."
            )
        return (
            "'event_id' is required, OR provide 'lookup_date' plus at least one of "
            "attendee_email / current_start_time / title_contains so the event can be looked "
            "up automatically."
        )

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else (input_str or {})
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        event_id            = data.get("event_id", "")
        title                = data.get("title", "")
        start_str            = data.get("start_time", "")
        duration_mins        = int(data.get("duration_mins", 0))
        description          = data.get("description", "")
        location             = data.get("location", "")
        add_attendees        = data.get("add_attendees", [])
        tz_name              = data.get("timezone", "Asia/Kolkata")
        lookup_date          = data.get("lookup_date", "")
        attendee_email       = data.get("attendee_email", "")
        current_start_time   = data.get("current_start_time", "")
        title_contains       = data.get("title_contains", "")

        has_valid_id = bool(event_id) and bool(_VALID_EVENT_ID_RE.match(event_id))

        if not has_valid_id and not self._has_lookup_filters(data):
            if event_id:
                return json.dumps({
                    "error": (
                        f"'{event_id}' is not a real Google Calendar event ID — it looks like a "
                        "guessed or made-up value. Call list_events or get_event first to get the "
                        "REAL event_id, or provide 'lookup_date' plus attendee_email/"
                        "current_start_time/title_contains so it can be looked up automatically."
                    )
                })
            return json.dumps({
                "error": (
                    "'event_id' is required, OR provide 'lookup_date' plus at least one of "
                    "attendee_email / current_start_time / title_contains."
                )
            })

        try:
            service = self._get_service()

            if not has_valid_id:
                resolved_id, err = _resolve_event_id(
                    service, lookup_date, attendee_email, current_start_time, title_contains, tz_name
                )
                if err:
                    return json.dumps(err)
                event_id = resolved_id

            # Fetch existing event
            event = service.events().get(calendarId="primary", eventId=event_id).execute()

            # Apply updates
            if title:
                event["summary"] = title
            if description:
                event["description"] = description
            if location:
                event["location"] = location

            if start_str:
                try:
                    start_dt = datetime.fromisoformat(start_str)
                except ValueError:
                    return json.dumps({"error": f"Cannot parse start_time: '{start_str}'"})

                # If duration_mins given, use it; else keep original duration
                if duration_mins > 0:
                    end_dt = start_dt + timedelta(minutes=duration_mins)
                else:
                    # Try to keep original duration
                    orig_start_str = event.get("start", {}).get("dateTime", "")
                    orig_end_str   = event.get("end",   {}).get("dateTime", "")
                    if orig_start_str and orig_end_str:
                        orig_start = datetime.fromisoformat(orig_start_str)
                        orig_end   = datetime.fromisoformat(orig_end_str)
                        orig_dur   = orig_end - orig_start
                        end_dt     = start_dt + orig_dur
                    else:
                        end_dt = start_dt + timedelta(hours=1)

                event["start"] = {"dateTime": start_dt.isoformat(), "timeZone": tz_name}
                event["end"]   = {"dateTime": end_dt.isoformat(),   "timeZone": tz_name}

            elif duration_mins > 0:
                # Change only duration, keep start
                orig_start_str = event.get("start", {}).get("dateTime", "")
                if orig_start_str:
                    orig_start = datetime.fromisoformat(orig_start_str)
                    end_dt     = orig_start + timedelta(minutes=duration_mins)
                    event["end"] = {"dateTime": end_dt.isoformat(), "timeZone": tz_name}

            # Add new attendees (preserve existing)
            if add_attendees:
                existing_emails = {a["email"] for a in event.get("attendees", [])}
                for email in add_attendees:
                    if email.strip() and email.strip() not in existing_emails:
                        event.setdefault("attendees", []).append({"email": email.strip()})

            updated = service.events().update(
                calendarId="primary",
                eventId=event_id,
                body=event,
                sendUpdates="all",
            ).execute()

            logger.info("UpdateEventTool: event updated id=%s", event_id)
            return json.dumps({
                "status":    "updated",
                "event_id":  updated.get("id", ""),
                "event_url": updated.get("htmlLink", ""),
                "title":     updated.get("summary", ""),
                "start":     updated.get("start", {}).get("dateTime", ""),
                "end":       updated.get("end",   {}).get("dateTime", ""),
            }, ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("UpdateEventTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "event_id":            {"type": "string",  "description": "Real Google Calendar event ID, if already known (optional if using lookup filters)"},
                "lookup_date":         {"type": "string",  "description": "YYYY-MM-DD — event's CURRENT date, required if event_id is not known"},
                "attendee_email":      {"type": "string",  "description": "Lookup filter: an attendee's email"},
                "current_start_time":  {"type": "string",  "description": "Lookup filter: event's CURRENT start time, HH:MM or ISO (not the new time)"},
                "title_contains":      {"type": "string",  "description": "Lookup filter: substring of the event title"},
                "title":               {"type": "string",  "description": "New event title (optional)"},
                "start_time":          {"type": "string",  "description": "NEW start time ISO 8601 to reschedule to (optional)"},
                "duration_mins":       {"type": "integer", "description": "New duration in minutes (optional)"},
                "description":         {"type": "string",  "description": "Updated description/agenda (optional)"},
                "location":            {"type": "string",  "description": "Updated location (optional)"},
                "add_attendees":       {"type": "array", "items": {"type": "string"},
                                        "description": "Additional attendees to add (optional)"},
                "timezone":            {"type": "string",  "description": "IANA timezone (default: Asia/Kolkata)"},
            }, "required": []},
        }}