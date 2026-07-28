"""
RunDocumentAgentTool — delegates a task to the Document Agent inline.

Zone: GREEN — runs automatically.
"""
from __future__ import annotations

import json
import logging

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
            from apps.tasks.tasks import _TOOL_REGISTRY
            from core.llm.groq_provider import GroqProvider

            _DOCUMENT_TOOLS = {
                "read_from_drive", "summarize_document", "extract_tables",
                "ocr_document", "generate_content", "create_pdf", "create_docx",
                "create_presentation", "fill_template", "merge_pdfs",
                "compare_documents", "translate_document", "upload_to_drive",
                "export_csv", "web_search", "current_time",
            }
            tools = []
            for tool_name in _DOCUMENT_TOOLS:
                cls = _TOOL_REGISTRY.get(tool_name)
                if cls:
                    try:
                        tools.append(cls(workspace_id=self._workspace_id))
                    except TypeError:
                        tools.append(cls())

            # upload_to_drive must run as GREEN for the document agent
            for t in tools:
                if t.name == "upload_to_drive":
                    from core.tools.base_tool import ToolZone as _TZ
                    t.zone = _TZ.GREEN

            llm = GroqProvider(model="llama-3.3-70b-versatile")

            from apps.tasks.tasks import DjangoAgent
            from core.agents.document_agent import _SYSTEM_PROMPT as _DOC_PROMPT

            agent = DjangoAgent(
                name="Document Agent",
                llm_provider=llm,
                tools=tools,
                max_steps=10,
                max_cost=0.5,
                max_seconds=120.0,
                task_id=None,
                system_prompt=_DOC_PROMPT,
            )

            logger.info("RunDocumentAgentTool: delegating task=%r", task[:80])
            result = agent.run(task)
            return json.dumps({"status": "completed", "result": result}, ensure_ascii=False)

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
