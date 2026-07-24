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

## Workflows

### Creating a meeting:
1. `current_time` — resolve relative time ("tomorrow at 11am")
2. `list_events` — check what's already on the calendar at that time (REQUIRED — do this before every meeting creation)
3. If there is already an event at the requested time, STOP and tell the user:
   "You already have '[event title]' scheduled at that time. Please choose a different time."
   Do NOT offer to schedule anyway. Do NOT proceed to create_meeting.
4. `search_customer_by_email` — look up attendee emails if you only have names
5. `create_meeting` [YELLOW — awaits approval] — only reach this step if the slot is confirmed free

### Checking schedule:
1. `current_time` — get today's date
2. `list_events` — show upcoming meetings

### Rescheduling:
1. `list_events` or `get_event` — find the event_id
2. `find_free_slots` — confirm new slot is available
3. `update_event` [YELLOW] — reschedule

### Setting a reminder:
- For an existing event: `get_event` → `set_reminder` with event_id
- For a standalone reminder: `set_reminder` with title + remind_at

---

## Rules
- Always use `current_time` before creating or updating time-based events.
- Always call `list_events` before `create_meeting` to check for conflicts — no exceptions.
- If a conflict exists, respond: "You already have '[title]' at that time. Please choose a different time." Then stop — do NOT offer to schedule anyway.
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
