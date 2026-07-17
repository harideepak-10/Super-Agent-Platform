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
        "Generate document content AND automatically create one or more files in one step. "
        "Input JSON: {\"title\": \"...\", \"doc_type\": \"report|summary|proposal|letter|table\", "
        "\"prompt\": \"...\", "
        "\"output_format\": \"pdf|docx|pptx\" (single format, default pdf), "
        "\"formats\": [\"pptx\", \"docx\"] (multiple formats — content generated ONCE, all files created), "
        "\"source_data\": \"...(optional)\", \"sections\": [...](optional)}. "
        "Use formats=[\"pptx\",\"docx\"] when user wants BOTH PowerPoint AND Word. "
        "Returns file_path (single) or files[] (multiple). "
        "Do NOT call create_pdf, create_docx, or create_presentation separately — this tool handles everything."
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
        author      = data.get("author", "KRYPSOS Agent")

        # Resolve the list of formats to produce
        _valid = {"pdf", "docx", "pptx"}
        raw_formats = data.get("formats", [])
        if raw_formats and isinstance(raw_formats, list):
            formats = [f.lower() for f in raw_formats if f.lower() in _valid]
        else:
            single = data.get("output_format", "pdf").lower()
            formats = [single if single in _valid else "pdf"]

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

        # Generate content ONCE — reuse for all formats
        written_sections = self._call_llm(system_msg, user_msg, section_list)

        # Create all requested files from the same content
        created_files = []
        for fmt in formats:
            file_result = self._auto_create_file(title, doc_type, written_sections, author, fmt)
            if file_result.get("status") == "created":
                created_files.append(file_result)

        result = {
            "title":    title,
            "doc_type": doc_type,
            "sections": written_sections,
        }

        if len(created_files) == 1:
            # Single format — flat response (backward compatible)
            result.update(created_files[0])
        elif len(created_files) > 1:
            # Multiple formats — return files list
            result["files"] = created_files
            result["status"] = "created"
            result["note"] = (
                f"{len(created_files)} files created: "
                + ", ".join(f["filename"] for f in created_files)
                + ". Pass each file_path to upload_to_drive to save to Google Drive."
            )
        else:
            result["error"] = "No files could be created."

        return json.dumps(result, ensure_ascii=False)

    def _auto_create_file(self, title: str, doc_type: str, sections: list, author: str, output_format: str = "pdf") -> dict:
        """Immediately build the output file (PDF, DOCX, or PPTX) so no second tool call is needed."""
        import json as _json
        try:
            if output_format == "pptx":
                from core.tools.document.create_presentation import CreatePresentationTool
                slides = self._sections_to_slides(sections)
                payload = _json.dumps({"title": title, "author": author, "slides": slides})
                raw = CreatePresentationTool().run(payload)
                result = _json.loads(raw)
                if result.get("status") == "created":
                    return {
                        "status":    "created",
                        "file_path": result["file_path"],
                        "filename":  result["filename"],
                        "slides":    result.get("slides", len(slides)),
                        "format":    "pptx",
                        "note": (
                            f"PowerPoint created at {result['file_path']}. "
                            "Call upload_to_drive with this file_path to save it to Google Drive."
                        ),
                    }
            else:
                payload = _json.dumps({"title": title, "sections": sections, "author": author})
                if output_format == "docx":
                    from core.tools.document.create_docx import CreateDocxTool
                    raw = CreateDocxTool().run(payload)
                else:
                    from core.tools.document.create_pdf import CreatePdfTool
                    raw = CreatePdfTool().run(payload)
                result = _json.loads(raw)
                if result.get("status") == "created":
                    fmt = result.get("format", output_format)
                    return {
                        "status":    "created",
                        "file_path": result["file_path"],
                        "filename":  result["filename"],
                        "size_kb":   result["size_kb"],
                        "format":    fmt,
                        "note": (
                            f"{fmt.upper()} created at {result['file_path']}. "
                            "Call upload_to_drive with this file_path to save it to Google Drive."
                        ),
                    }
        except Exception as exc:
            return {"file_error": str(exc)}
        return {}

    @staticmethod
    def _sections_to_slides(sections: list) -> list:
        """Convert generate_content sections → create_presentation slides format."""
        import re
        slides = []
        for sec in sections:
            heading = sec.get("heading", "Slide")
            content = sec.get("content", "")
            # Split content into bullet points (by sentence or newline)
            raw_bullets = [b.strip() for b in re.split(r"\n|(?<=[.!?])\s+", content) if b.strip()]
            # Keep bullets short — max 120 chars each, max 6 per slide
            bullets = [b[:120] for b in raw_bullets if len(b) > 5][:6]
            slides.append({
                "title":   heading,
                "content": content[:200],   # brief slide body text
                "bullets": bullets,
            })
        return slides

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
                    "title":         {"type": "string", "description": "Document title"},
                    "doc_type":      {"type": "string", "enum": ["report", "summary", "proposal", "letter", "table"]},
                    "prompt":        {"type": "string", "description": "What to write"},
                    "output_format": {"type": "string", "enum": ["pdf", "docx", "pptx"],
                                      "description": "Single output format: 'pdf' (default), 'docx' for Word, 'pptx' for PowerPoint."},
                    "formats":       {"type": "array", "items": {"type": "string", "enum": ["pdf", "docx", "pptx"]},
                                      "description": "Multiple output formats in one call e.g. [\"pptx\", \"docx\"]. Content is generated once and all files are created."},
                    "source_data":   {"type": "string", "description": "Optional raw content to base the doc on"},
                    "sections":      {"type": "array",  "items": {"type": "string"}, "description": "Optional list of section headings"},
                },
                "required": ["title", "doc_type", "prompt"],
            },
        }}
