"""
RunReportingAgentTool — delegates a task to the Reporting Agent inline.

Zone: GREEN — the Reporting Agent's tools are all read-only/GREEN, but this
still handles ApprovalRequired in case a YELLOW tool is ever added to its
template later (e.g. an "email this report" step).

Tools, model, and system prompt are pulled live from the "reporting" template
in apps/agents/views.py — NOT a hardcoded list — so this can never drift out
of sync with what the real, standalone Reporting Agent is configured to do.
"""
from __future__ import annotations

import json
import logging

from core.base_agent import ApprovalRequired
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class RunReportingAgentTool(BaseTool):
    """Delegate a task to the Reporting Agent.

    Use for anything that asks for a report, summary, or statistics about
    the system's own activity — tasks, agents, emails sent, meetings created,
    documents produced, productivity, execution history, success/failure
    rates, tool usage, or token/cost usage — including EOD/daily/weekly/
    monthly reports and combined business reports.

    Input::

        {"task": "generate today's EOD report as a PDF"}

    Returns the Reporting Agent's result string.
    """

    name: str = "run_reporting_agent"
    description: str = (
        "Delegate a task to the Reporting Agent. "
        "Use for ANY reporting/statistics task about the system itself: daily/weekly/monthly "
        "EOD reports, task or agent activity, email activity, meeting activity, document/file "
        "activity, productivity summaries, execution history, success/failure statistics, tool "
        "usage, token/cost usage, or a combined business report. "
        "Input JSON: {\"task\": \"<what report to generate, including period and output format if given>\"}"
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

            rpt_tmpl = _TEMPLATE_AGENT_TYPE_MAP.get("reporting", {})

            tools = []
            for tool_name in rpt_tmpl.get("tools", []):
                cls = _TOOL_REGISTRY.get(tool_name)
                if cls:
                    try:
                        tools.append(cls(workspace_id=self._workspace_id))
                    except TypeError:
                        tools.append(cls())

            llm_model = rpt_tmpl.get("llm_model") or "llama-3.3-70b-versatile"
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
            system_prompt = (rpt_tmpl.get("system_prompt", "") or "") + _today_context

            agent = DjangoAgent(
                name="Reporting Agent",
                llm_provider=llm,
                tools=tools,
                max_steps=rpt_tmpl.get("max_steps", 20),
                max_cost=min(float(rpt_tmpl.get("max_cost_usd", 1.0)), 0.5),
                max_seconds=120.0,
                task_id=None,
                system_prompt=system_prompt,
            )

            logger.info("RunReportingAgentTool: delegating task=%r", task[:80])
            result = agent.run(task)
            return json.dumps({"status": "completed", "result": result}, ensure_ascii=False)

        except ApprovalRequired as exc:
            if isinstance(exc.snapshot, dict):
                exc.snapshot["_nested_kind"] = "reporting"
            raise

        except Exception as exc:
            logger.exception("RunReportingAgentTool failed")
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
                        "description": "The reporting task to perform, in plain English — include period and output format if specified.",
                    },
                },
                "required": ["task"],
            },
        }}