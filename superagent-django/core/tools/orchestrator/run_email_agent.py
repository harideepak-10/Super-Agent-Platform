"""
RunEmailAgentTool — delegates a task to the Email Agent inline.

Zone: GREEN — runs automatically (the sub-agent may itself require approvals
for high-risk tools like send_email, but routing is automatic).

Tools, model, and system prompt are pulled live from the "email" template in
apps/agents/views.py — NOT a separately hardcoded list — so this can never
drift out of sync with what the real, standalone Email Agent is configured
to do.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.base_agent import ApprovalRequired
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class RunEmailAgentTool(BaseTool):
    """Delegate a task to the Email Agent.

    Use for anything involving Gmail: reading, searching, summarising,
    drafting, replying, forwarding, or sending emails.

    Input::

        {
            "task": "summarise my unread emails from today"
        }

    Returns the Email Agent's result string.
    """

    name: str = "run_email_agent"
    description: str = (
        "Delegate a task to the Email Agent. "
        "Use for ANY email/Gmail task: read, search, summarise, draft, reply, send. "
        "Input JSON: {\"task\": \"<what to do with email>\"}"
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

            email_tmpl = _TEMPLATE_AGENT_TYPE_MAP.get("email", {})

            tools = []
            for tool_name in email_tmpl.get("tools", []):
                cls = _TOOL_REGISTRY.get(tool_name)
                if cls:
                    try:
                        tools.append(cls(workspace_id=self._workspace_id))
                    except TypeError:
                        tools.append(cls())

            llm_model = email_tmpl.get("llm_model") or "llama-3.3-70b-versatile"
            if llm_model.startswith("claude-"):
                from core.llm.anthropic_provider import AnthropicProvider
                llm = AnthropicProvider(model=llm_model)
            else:
                from core.llm.groq_provider import GroqProvider
                llm = GroqProvider(model=llm_model)

            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo as _ZoneInfo
            _now_ist = _dt.now(_ZoneInfo("Asia/Kolkata"))
            _today_context = (
                f"\n\n[SYSTEM CONTEXT] Today's real date is {_now_ist.strftime('%Y-%m-%d')} "
                f"({_now_ist.strftime('%A')}), current time is {_now_ist.strftime('%H:%M')} IST. "
                "Always treat this as 'today' for any date calculation — never guess or reuse a "
                "stale date, since today changes every day."
            )
            system_prompt = (email_tmpl.get("system_prompt", "") or "") + _today_context

            agent = DjangoAgent(
                name="Email Agent",
                llm_provider=llm,
                tools=tools,
                max_steps=email_tmpl.get("max_steps", 8),
                max_cost=min(float(email_tmpl.get("max_cost_usd", 1.0)), 0.5),
                max_seconds=120.0,
                task_id=None,
                system_prompt=system_prompt,
            )

            logger.info("RunEmailAgentTool: delegating task=%r", task[:80])
            result = agent.run(task)
            return json.dumps({"status": "completed", "result": result}, ensure_ascii=False)

        except ApprovalRequired as exc:
            if isinstance(exc.snapshot, dict):
                exc.snapshot["_nested_kind"] = "email"
            raise

        except Exception as exc:
            logger.exception("RunEmailAgentTool failed")
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
                        "description": "The email task to perform, in plain English.",
                    },
                },
                "required": ["task"],
            },
        }}