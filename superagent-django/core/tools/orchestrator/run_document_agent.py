"""
RunDocumentAgentTool — delegates a task to the Document Agent inline.

Zone: GREEN — runs automatically.

Tools, model, and system prompt are pulled live from the "document" template
in apps/agents/views.py — NOT a separately hardcoded list — so this can
never drift out of sync with what the real, standalone Document Agent is
configured to do.
"""
from __future__ import annotations

import json
import logging

from core.base_agent import ApprovalRequired
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class RunDocumentAgentTool(BaseTool):
    """Delegate a task to the Document Agent.

    Use for anything involving documents or Google Drive: translate, summarise,
    create Word/PDF/PPT, read from Drive, upload to Drive, OCR, compare docs.

    Input::

        {
            "task": "translate the aura clinic api doc in my drive to Tamil and save as docx"
        }

    Returns the Document Agent's result string.
    """

    name: str = "run_document_agent"
    description: str = (
        "Delegate a task to the Document Agent. "
        "Use for ANY document/Drive task: translate, summarise, create Word/PDF/PPT, "
        "read from Drive, upload to Drive, OCR, compare documents. "
        "Input JSON: {\"task\": \"<what to do with the document>\"}"
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

            doc_tmpl = _TEMPLATE_AGENT_TYPE_MAP.get("document", {})

            tools = []
            for tool_name in doc_tmpl.get("tools", []):
                cls = _TOOL_REGISTRY.get(tool_name)
                if cls:
                    try:
                        tools.append(cls(workspace_id=self._workspace_id))
                    except TypeError:
                        tools.append(cls())

            llm_model = doc_tmpl.get("llm_model") or "llama-3.3-70b-versatile"
            if llm_model.startswith("claude-"):
                from core.llm.anthropic_provider import AnthropicProvider
                llm = AnthropicProvider(model=llm_model)
            else:
                from core.llm.groq_provider import GroqProvider
                llm = GroqProvider(model=llm_model)

            system_prompt = doc_tmpl.get("system_prompt", "")

            agent = DjangoAgent(
                name="Document Agent",
                llm_provider=llm,
                tools=tools,
                max_steps=doc_tmpl.get("max_steps", 10),
                max_cost=min(float(doc_tmpl.get("max_cost_usd", 1.0)), 0.5),
                max_seconds=120.0,
                task_id=None,
                system_prompt=system_prompt,
            )

            logger.info("RunDocumentAgentTool: delegating task=%r", task[:80])
            result = agent.run(task)
            return json.dumps({"status": "completed", "result": result}, ensure_ascii=False)

        except ApprovalRequired as exc:
            if isinstance(exc.snapshot, dict):
                exc.snapshot["_nested_kind"] = "document"
            raise

        except Exception as exc:
            logger.exception("RunDocumentAgentTool failed")
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
                        "description": "The document task to perform, in plain English.",
                    },
                },
                "required": ["task"],
            },
        }}