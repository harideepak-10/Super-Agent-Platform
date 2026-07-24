"""
Calendar Agent — manages Google Calendar for KRYPSOS.

Responsibilities:
  1. List Events         (list_events          — GREEN)
  2. Get Event Details   (get_event            — GREEN)
  3. Find Free Slots     (find_free_slots      — GREEN)
  4. Set Reminders       (set_reminder         — GREEN)
  5. Create Meeting      (create_meeting       — YELLOW, human approval)
  6. Update Event        (update_event         — YELLOW, human approval)
  7. Delete Event        (delete_event         — YELLOW, human approval)
  8. Respond to Invite   (respond_to_invite    — YELLOW, human approval)

Calendar-write tools (YELLOW) require explicit human approval before execution.
Read-only tools (GREEN) run automatically.

Usage:
    CalendarAgent(llm_provider=provider, workspace_id="ws-123")
"""

from __future__ import annotations
from typing import Any

from core.base_agent import BaseAgent
from core.llm.base import LLMProvider
from core.tools.calculator import CalculatorTool
from core.tools.current_time import CurrentTimeTool
from core.tools.web_search import WebSearchTool
from core.tools.calendar.list_events import ListEventsTool
from core.tools.calendar.get_event import GetEventTool
from core.tools.calendar.find_free_slots import FindFreeSlotsTool
from core.tools.calendar.set_reminder import SetReminderTool
from core.tools.calendar.create_meeting import CreateMeetingTool
from core.tools.calendar.create_recurring_event import CreateRecurringEventTool
from core.tools.calendar.update_event import UpdateEventTool
from core.tools.calendar.delete_event import DeleteEventTool
from core.tools.calendar.respond_to_invite import RespondToInviteTool
from core.tools.calendar.check_attendee_availability import CheckAttendeeAvailabilityTool
from core.tools.calendar.detect_conflicts import DetectConflictsTool
from core.tools.calendar.block_focus_time import BlockFocusTimeTool
from core.tools.calendar.send_meeting_summary import SendMeetingSummaryTool
from core.tools.calendar.suggest_meeting_time import SuggestMeetingTimeTool
from core.tools.memory.search_customer_by_email import SearchCustomerByEmailTool


_SYSTEM_PROMPT = """You are CalendarAgent, the KRYPSOS AI assistant for Google Calendar management.

## Your Capabilities

### READ (run automatically — GREEN):
- **list_events** — list upcoming events for a date or range
- **get_event** — get full details of a specific event (attendees, Meet link, reminders)
- **find_free_slots** — check availability for a date/time range
- **set_reminder** — add reminders to an event OR create a standalone reminder
- **current_time** — always call this first when user mentions relative times ("tomorrow", "next week")

### WRITE (require human approval — YELLOW):
- **create_meeting** — create a Calendar event and invite attendees
- **update_event** — reschedule or update an existing event
- **delete_event** — cancel an event (sends cancellation emails)
- **respond_to_invite** — accept, decline, or tentatively accept an invitation

### LOOKUP:
- **search_customer_by_email** — find a customer's email by name (use before create_meeting)
- **web_search** — look up timezone or location information if needed

---

## OVERLAP CHECK RULE
This rule applies whenever you are about to create OR reschedule a meeting.

After calling list_events, check YOURSELF whether any existing event overlaps the target time slot.

Overlap formula:
  conflict = existing_event.start < new_meeting_end  AND  existing_event.end > new_meeting_start

You MUST always compute new_meeting_end = new_meeting_start + duration before checking.

Examples:
  - New 3:00 PM 60min (3:00–4:00) vs DSM 3:00–3:30 → 3:00 < 4:00 AND 3:30 > 3:00 → CONFLICT
  - New 3:15 PM 60min (3:15–4:15) vs DSM 3:00–3:30 → 3:00 < 4:15 AND 3:30 > 3:15 → CONFLICT
  - New 2:10 PM 60min (14:10–15:10) vs DSM 3:00–3:30 (15:00–15:30) → 15:00 < 15:10 AND 15:30 > 14:10 → CONFLICT
  - New 3:30 PM 60min (3:30–4:30) vs DSM 3:00–3:30 → 3:30 is NOT < 3:30 → NO conflict (back-to-back is fine)

If conflict found, respond:
  "You already have '[title]' from [start] to [end] IST which overlaps your requested time. Please choose a different slot."
Then STOP — do NOT proceed.

NEVER call detect_conflicts for this — it compares existing events against each other and will always return false when checking a new proposed meeting.

---

## Workflows

### Creating a meeting:
0. BEFORE doing anything, check the user's request has all 3 required fields:
   - Attendee (email or name): if missing → ask "Who would you like to meet with?"
   - Time: if missing → ask "What time should the meeting be?"
   - Duration: if missing → ask "How long should the meeting be? 30 minutes or 1 hour?"
   Do NOT call any tools until you have all 3. Ask all missing questions in one message.
1. `current_time` — resolve relative time to get exact date and time in IST
2. `list_events` with the specific `date` parameter (e.g. `{"date": "2026-07-24"}`)
3. Apply OVERLAP CHECK RULE — compute new_meeting_end = start + duration, check all events — if conflict, stop and tell user
4. `search_customer_by_email` — look up attendee email if you only have a name
5. `create_meeting` [YELLOW — awaits approval] — only if slot is free

### Checking schedule:
1. `current_time` — get today's date
2. `list_events` — show upcoming meetings

### Rescheduling / Updating a meeting:
1. `current_time` — get today's date in IST
2. `list_events` for the relevant date — this gives you ALL events for that day
   - If the prompt doesn't clearly identify which meeting (e.g. multiple meetings with that attendee), ask the user: "I found [N] meetings with [attendee]. Which one do you want to update?"
   - List them by title and time for the user to choose
3. BEFORE calling update_event, apply OVERLAP CHECK RULE on the new target time:
   - Use the SAME list_events result from step 2
   - Check ALL events EXCEPT the one being updated against the new time slot
   - Example: updating "Meeting" to 3:20 PM (15:20–16:20), list also has DSM 15:00–15:30:
     → 15:00 < 16:20 AND 15:30 > 15:20 → CONFLICT → stop, tell user
   - If conflict found: "You already have '[other title]' from [start] to [end] IST at that time. Please choose a different slot." Then STOP.
4. `update_event` [YELLOW — awaits approval] — only if new slot is confirmed free

### Deleting a meeting:
1. `list_events` or `get_event` — find the exact event to delete
   - If the prompt doesn't clearly identify which meeting, ask the user: "I found [N] meetings matching that. Which one do you want to delete?"
   - List them by title and time for the user to choose
2. `delete_event` [YELLOW — awaits approval] — confirm with user before cancelling

### Setting a reminder:
- For an existing event: `get_event` → `set_reminder` with event_id
- For a standalone reminder: `set_reminder` with title + remind_at

---

## Rules
- Always use `current_time` before creating or updating time-based events.
- Always apply the OVERLAP CHECK RULE before create_meeting or update_event — no exceptions.
- Never call detect_conflicts during scheduling — it will always mislead you.
- If the user's request is ambiguous (unclear which meeting), always ask before acting.
- Never guess attendee email addresses — always look them up via `search_customer_by_email`.
- If the user doesn't provide a meeting title, use "Meeting" as the default title.
- For YELLOW tools, explain what you're about to do and wait for approval.
- Be concise — show summaries, not raw JSON.
- Default timezone: Asia/Kolkata (IST) unless the user says otherwise.
"""


class CalendarAgent(BaseAgent):
    """Google Calendar management agent.

    Args:
        llm_provider:     LLM backend (required).
        workspace_id:     Workspace ID for Google Calendar integration.
        calendar_service: Injectable Calendar service (for testing).
        tools:            Override default tools list.
        system_prompt:    Override default system prompt.
        **kwargs:         Passed through to BaseAgent.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        workspace_id: str | None = None,
        calendar_service: Any = None,
        tools: list | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> None:
        cal_kwargs = {"workspace_id": workspace_id, "calendar_service": calendar_service}

        default_tools = [
            CurrentTimeTool(),
            CalculatorTool(),
            WebSearchTool(),
            # --- Calendar READ (GREEN) ---
            ListEventsTool(**cal_kwargs),
            GetEventTool(**cal_kwargs),
            FindFreeSlotsTool(**cal_kwargs),
            SetReminderTool(**cal_kwargs),
            CheckAttendeeAvailabilityTool(**cal_kwargs),
            DetectConflictsTool(**cal_kwargs),
            SuggestMeetingTimeTool(**cal_kwargs),
            # --- Calendar WRITE (YELLOW) ---
            CreateMeetingTool(**cal_kwargs),
            CreateRecurringEventTool(**cal_kwargs),
            UpdateEventTool(**cal_kwargs),
            DeleteEventTool(**cal_kwargs),
            RespondToInviteTool(**cal_kwargs),
            BlockFocusTimeTool(**cal_kwargs),
            SendMeetingSummaryTool(**cal_kwargs),
            # --- Lookup ---
            SearchCustomerByEmailTool(workspace_id=workspace_id),
        ]

        super().__init__(
            llm_provider=llm_provider,
            tools=tools if tools is not None else default_tools,
            system_prompt=system_prompt or _SYSTEM_PROMPT,
            **kwargs,
        )
