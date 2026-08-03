"""
RunCalendarAgentTool — delegates a task to the Calendar Agent inline.

Zone: GREEN — runs automatically (the sub-agent may itself require approvals
for high-risk tools like create_meeting/update_event/delete_event).

Tools, model, and system prompt are pulled live from the "calendar" template
in apps/agents/views.py — NOT a hardcoded list — so this can never drift out
of sync with what the real, standalone Calendar Agent is configured to do.
"""
from __future__ import annotations

import json
import logging

from core.base_agent import ApprovalRequired
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class RunCalendarAgentTool(BaseTool):
    """Delegate a task to the Calendar Agent.

    Use for anything involving Google Calendar: viewing, scheduling,
    rescheduling, cancelling meetings, checking availability, reminders.

    Input::

        {
            "task": "create a meeting with harideepak.s10@gmail.com tomorrow at 3pm for 30 minutes"
        }

    Returns the Calendar Agent's result string.
    """

    name: str = "run_calendar_agent"
    description: str = (
        "Delegate a task to the Calendar Agent. "
        "Use for ANY Google Calendar task: view schedule, create/reschedule/cancel meetings, "
        "check availability, find free slots, set reminders, RSVP to invites. "
        "Input JSON: {\"task\": \"<what to do with the calendar>\"}"
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, workspace_id: str | None = None) -> None:
        self._workspace_id = workspace_id

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input. Expected JSON with 'task' key."})

        task = data.get("task", "").strip()
        if not task:
            return json.dumps({"error": "'task' is required."})

        try:
            from apps.tasks.tasks import _TOOL_REGISTRY, DjangoAgent
            from apps.agents.views import _TEMPLATE_AGENT_TYPE_MAP

            cal_tmpl = _TEMPLATE_AGENT_TYPE_MAP.get("calendar", {})

            tools = []
            for tool_name in cal_tmpl.get("tools", []):
                cls = _TOOL_REGISTRY.get(tool_name)
                if cls:
                    try:
                        tools.append(cls(workspace_id=self._workspace_id))
                    except TypeError:
                        tools.append(cls())

            llm_model = cal_tmpl.get("llm_model") or "llama-3.3-70b-versatile"
            if llm_model.startswith("claude-"):
                from core.llm.anthropic_provider import AnthropicProvider
                llm = AnthropicProvider(model=llm_model)
            else:
                from core.llm.groq_provider import GroqProvider
                llm = GroqProvider(model=llm_model)

            system_prompt = cal_tmpl.get("system_prompt", "")

            agent = DjangoAgent(
                name="Calendar Agent",
                llm_provider=llm,
                tools=tools,
                max_steps=cal_tmpl.get("max_steps", 20),
                max_cost=min(float(cal_tmpl.get("max_cost_usd", 1.0)), 0.5),
                max_seconds=120.0,
                task_id=None,
                system_prompt=system_prompt,
            )

            logger.info("RunCalendarAgentTool: delegating task=%r", task[:80])
            result = agent.run(task)
            return json.dumps({"status": "completed", "result": result}, ensure_ascii=False)

        except ApprovalRequired as exc:
            if isinstance(exc.snapshot, dict):
                exc.snapshot["_nested_kind"] = "calendar"
            raise

        except Exception as exc:
            logger.exception("RunCalendarAgentTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The calendar task to perform, in plain English.",
                    },
                },
                "required": ["task"],
            },
        }}