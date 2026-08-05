"""
RunFinanceAgentTool — delegates a task to the Finance Agent inline.

Zone: GREEN — runs automatically (Finance Agent currently has no YELLOW
tools of its own, but this still handles ApprovalRequired in case one is
added later).

Tools, model, and system prompt are pulled live from the "finance" template
in apps/agents/views.py — NOT a hardcoded list — so this can never drift out
of sync with what the real, standalone Finance Agent is configured to do.
"""
from __future__ import annotations

import json
import logging

from core.base_agent import ApprovalRequired
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class RunFinanceAgentTool(BaseTool):
    """Delegate a task to the Finance Agent.

    Use for anything involving expenses, invoices, budgets, currency
    conversion, or financial documents.

    Input::

        {
            "task": "generate an invoice for Acme Corp, 5 hours consulting at 2000 each, 18% tax"
        }

    Returns the Finance Agent's result string.
    """

    name: str = "run_finance_agent"
    description: str = (
        "Delegate a task to the Finance Agent. "
        "Use for ANY finance task: categorising expenses, budget/savings/tax calculations, "
        "generating invoices, summarising financial documents, currency conversion, or "
        "finding invoice emails. "
        "Input JSON: {\"task\": \"<what to do with finances>\"}"
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

            fin_tmpl = _TEMPLATE_AGENT_TYPE_MAP.get("finance", {})

            tools = []
            for tool_name in fin_tmpl.get("tools", []):
                cls = _TOOL_REGISTRY.get(tool_name)
                if cls:
                    try:
                        tools.append(cls(workspace_id=self._workspace_id))
                    except TypeError:
                        tools.append(cls())

            llm_model = fin_tmpl.get("llm_model") or "llama-3.3-70b-versatile"
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
            system_prompt = (fin_tmpl.get("system_prompt", "") or "") + _today_context

            agent = DjangoAgent(
                name="Finance Agent",
                llm_provider=llm,
                tools=tools,
                max_steps=fin_tmpl.get("max_steps", 20),
                max_cost=min(float(fin_tmpl.get("max_cost_usd", 1.0)), 0.5),
                max_seconds=120.0,
                task_id=None,
                system_prompt=system_prompt,
            )

            logger.info("RunFinanceAgentTool: delegating task=%r", task[:80])
            result = agent.run(task)
            return json.dumps({"status": "completed", "result": result}, ensure_ascii=False)

        except ApprovalRequired as exc:
            if isinstance(exc.snapshot, dict):
                exc.snapshot["_nested_kind"] = "finance"
            raise

        except Exception as exc:
            logger.exception("RunFinanceAgentTool failed")
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
                        "description": "The finance task to perform, in plain English.",
                    },
                },
                "required": ["task"],
            },
        }}