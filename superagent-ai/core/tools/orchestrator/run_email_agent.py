"""
RunEmailAgentTool — delegates a task to the Email Agent inline.

Zone: GREEN — runs automatically (the sub-agent may itself require approvals
for high-risk tools like send_email, but routing is automatic).
"""
from __future__ import annotations

import json
import logging
from typing import Any

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
            from apps.tasks.tasks import _TOOL_REGISTRY
            from core.llm.groq_provider import GroqProvider

            # Collect email tools only
            _EMAIL_TOOLS = {
                "send_email", "read_email", "search_emails",
                "read_email_attachment_content", "summarize_emails",
                "download_attachment", "create_draft", "create_gmail_draft",
                "mark_as_read", "label_email", "move_to_folder", "delete_email",
                "reply_to_email", "forward_email", "schedule_email",
                "extract_invoice_data", "detect_follow_up_needed",
                "read_attachment_content", "extract_data_from_attachment",
                "list_customer_profiles", "search_customer_by_email",
                "web_search", "current_time",
            }
            tools = []
            for tool_name in _EMAIL_TOOLS:
                cls = _TOOL_REGISTRY.get(tool_name)
                if cls:
                    try:
                        tools.append(cls(workspace_id=self._workspace_id))
                    except TypeError:
                        tools.append(cls())

            llm = GroqProvider(model="llama-3.3-70b-versatile")

            from apps.tasks.tasks import DjangoAgent
            from apps.agents.views import _TEMPLATE_AGENT_TYPE_MAP

            email_tmpl = _TEMPLATE_AGENT_TYPE_MAP.get("email", {})
            system_prompt = email_tmpl.get("system_prompt", "")

            agent = DjangoAgent(
                name="Email Agent",
                llm_provider=llm,
                tools=tools,
                max_steps=8,
                max_cost=0.5,
                max_seconds=120.0,
                task_id=None,
                system_prompt=system_prompt,
            )

            logger.info("RunEmailAgentTool: delegating task=%r", task[:80])
            result = agent.run(task)
            return json.dumps({"status": "completed", "result": result}, ensure_ascii=False)

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
