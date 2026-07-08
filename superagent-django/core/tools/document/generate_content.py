"""
GenerateContentTool — LLM generates structured document content.

Zone: GREEN — runs automatically, no human approval required.

Takes a prompt describing what to write, the document type, and optional
source data (email content, task history, raw text). Returns structured
content ready to be passed to create_pdf / create_docx / export_csv.
"""

from __future__ import annotations

import json
from core.tools.base_tool import BaseTool, ToolZone


class GenerateContentTool(BaseTool):
    """Generate structured document content using the LLM.

    Input format (JSON string)::

        {
            "title":       "Q2 Sales Report",
            "doc_type":    "report",          # report | summary | proposal | letter | table
            "prompt":      "Write a Q2 sales report covering...",
            "source_data": "...optional raw text, email content, or task history...",
            "sections":    ["Executive Summary", "Key Metrics", "Recommendations"]  # optional
        }

    Returns::

        {
            "title":    "Q2 Sales Report",
            "doc_type": "report",
            "sections": [
                {"heading": "Executive Summary", "content": "..."},
                {"heading": "Key Metrics",       "content": "..."},
                ...
            ],
            "ready_for": ["create_pdf", "create_docx"]
        }
    """

    name: str = "generate_content"
    description: str = (
        "Generate structured document content using the LLM. "
        "Input JSON: {\"title\": \"...\", \"doc_type\": \"report|summary|proposal|letter|table\", "
        "\"prompt\": \"...\", \"source_data\": \"...(optional)\", \"sections\": [...](optional)}. "
        "Returns structured sections ready to pass to create_pdf or create_docx. "
        "Always call this first before creating any document file."
    )
    zone: ToolZone = ToolZone.GREEN

    _DOC_TYPE_INSTRUCTIONS = {
        "report": (
            "Write a professional business report. Use clear section headings. "
            "Include an executive summary, detailed findings, and recommendations. "
            "Be specific with any numbers, dates, or metrics mentioned."
        ),
        "summary": (
            "Write a concise summary. Cover the main points clearly and briefly. "
            "Use bullet points within sections where appropriate."
        ),
        "proposal": (
            "Write a professional business proposal. Include an overview, "
            "objectives, approach, timeline, and next steps."
        ),
        "letter": (
            "Write a professional business letter. Use formal tone. "
            "Include greeting, body paragraphs, and closing."
        ),
        "table": (
            "Organise the data into a clear tabular structure. "
            "Return sections as rows with consistent columns."
        ),
    }

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input. Expected JSON."})

        title       = data.get("title", "Document")
        doc_type    = data.get("doc_type", "report").lower()
        prompt      = data.get("prompt", "")
        source_data = data.get("source_data", "")
        sections    = data.get("sections", [])

        if not prompt and not source_data:
            return json.dumps({"error": "Either 'prompt' or 'source_data' is required."})

        type_instruction = self._DOC_TYPE_INSTRUCTIONS.get(
            doc_type, self._DOC_TYPE_INSTRUCTIONS["report"]
        )

        # Build the instruction for the agent's LLM to act on
        instruction_parts = [
            f"Document title: {title}",
            f"Document type: {doc_type}",
            f"Instructions: {type_instruction}",
        ]
        if prompt:
            instruction_parts.append(f"User request: {prompt}")
        if source_data:
            instruction_parts.append(f"Source data to use:\n{source_data[:3000]}")
        if sections:
            instruction_parts.append(f"Required sections: {', '.join(sections)}")

        return json.dumps({
            "title":    title,
            "doc_type": doc_type,
            "instruction": "\n\n".join(instruction_parts),
            "suggested_sections": sections or self._default_sections(doc_type),
            "ready_for": ["create_pdf", "create_docx"] if doc_type != "table" else ["export_csv", "create_pdf"],
            "note": (
                "Use the instruction above to write the full document content. "
                "Then call create_pdf or create_docx with the title and your written sections. "
                "Each section needs a 'heading' and 'content' field."
            ),
        }, ensure_ascii=False)

    @staticmethod
    def _default_sections(doc_type: str) -> list[str]:
        defaults = {
            "report":   ["Executive Summary", "Background", "Key Findings", "Analysis", "Recommendations"],
            "summary":  ["Overview", "Key Points", "Conclusion"],
            "proposal": ["Overview", "Objectives", "Approach", "Timeline", "Next Steps"],
            "letter":   ["Introduction", "Main Body", "Closing"],
            "table":    ["Data"],
        }
        return defaults.get(doc_type, ["Introduction", "Main Content", "Conclusion"])

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "title":       {"type": "string", "description": "Document title"},
                    "doc_type":    {"type": "string", "enum": ["report", "summary", "proposal", "letter", "table"]},
                    "prompt":      {"type": "string", "description": "What to write"},
                    "source_data": {"type": "string", "description": "Optional raw content to base the doc on"},
                    "sections":    {"type": "array",  "items": {"type": "string"}, "description": "Optional list of section headings"},
                },
                "required": ["title", "doc_type", "prompt"],
            },
        }}
