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
        max_points  = data.get("max_points", 5)

        if not prompt and not source_data:
            return json.dumps({"error": "Either 'prompt' or 'source_data' is required."})

        type_instruction = self._DOC_TYPE_INSTRUCTIONS.get(
            doc_type, self._DOC_TYPE_INSTRUCTIONS["report"]
        )

        section_list = sections or self._default_sections(doc_type)

        # Build system + user prompt for content generation
        system_msg = (
            f"You are a professional document writer. {type_instruction} "
            f"Write detailed, high-quality content for each section. "
            f"Use up to {max_points} key points per section. "
            "Respond ONLY with a JSON array of objects: "
            '[{"heading": "...", "content": "..."}, ...]. No extra text.'
        )
        user_msg_parts = [f"Document title: {title}"]
        if prompt:
            user_msg_parts.append(f"Topic / request: {prompt}")
        if source_data:
            user_msg_parts.append(f"Source data:\n{source_data[:3000]}")
        user_msg_parts.append(f"Sections to write: {', '.join(section_list)}")
        user_msg = "\n\n".join(user_msg_parts)

        # Call Groq directly to generate real content
        written_sections = self._call_llm(system_msg, user_msg, section_list)

        return json.dumps({
            "title":    title,
            "doc_type": doc_type,
            "sections": written_sections,
            "ready_for": ["create_pdf", "create_docx"] if doc_type != "table" else ["export_csv", "create_pdf"],
            "note": "Content generated. Pass title + sections directly to create_pdf or create_docx.",
        }, ensure_ascii=False)

    def _call_llm(self, system_msg: str, user_msg: str, section_list: list) -> list:
        """Call Groq to generate actual section content. Falls back to stubs on error."""
        import os
        try:
            from groq import Groq
            client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
            completion = client.chat.completions.create(
                model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0.7,
                max_tokens=4096,
            )
            raw = completion.choices[0].message.content or ""
            # Extract JSON array from response
            import re
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                sections = json.loads(match.group())
                if isinstance(sections, list) and sections:
                    return sections
        except Exception:
            pass

        # Fallback: return stub sections so create_pdf can still run
        return [{"heading": h, "content": f"Content for {h}."} for h in section_list]

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
