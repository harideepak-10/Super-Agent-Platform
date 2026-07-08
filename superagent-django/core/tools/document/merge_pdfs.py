"""
MergePdfsTool — combine multiple PDF files into one.

Zone: GREEN — runs automatically, no human approval required.
"""
from __future__ import annotations
import json
import logging
import os
import tempfile
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class MergePdfsTool(BaseTool):
    """Combine multiple PDF files into a single PDF.

    Input::

        {
            "file_paths":    ["/tmp/report.pdf", "/tmp/appendix.pdf"],
            "output_filename": "combined_report.pdf"    # optional
        }

    Returns::

        {
            "status":    "merged",
            "file_path": "/tmp/combined_report.pdf",
            "filename":  "combined_report.pdf",
            "pages":     12,
            "files_merged": 2
        }
    """

    name: str = "merge_pdfs"
    description: str = (
        "Combine multiple PDF files into a single PDF. GREEN — runs automatically. "
        "Input JSON: {\"file_paths\": [\"/tmp/doc1.pdf\", \"/tmp/doc2.pdf\"], "
        "\"output_filename\": \"combined.pdf\"}. "
        "Returns file_path — pass to upload_to_drive to save to Google Drive."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, workspace_id: str | None = None) -> None:
        self._workspace_id = workspace_id

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        file_paths      = data.get("file_paths", [])
        output_filename = data.get("output_filename", "merged.pdf")

        if not file_paths or len(file_paths) < 2:
            return json.dumps({"error": "'file_paths' must contain at least 2 PDF paths."})

        missing = [p for p in file_paths if not os.path.exists(p)]
        if missing:
            return json.dumps({"error": f"Files not found: {missing}"})

        try:
            import pypdf
            writer = pypdf.PdfWriter()

            for path in file_paths:
                reader = pypdf.PdfReader(path)
                for page in reader.pages:
                    writer.add_page(page)

            out_path = os.path.join(tempfile.gettempdir(), output_filename)
            with open(out_path, "wb") as f:
                writer.write(f)

            total_pages = len(writer.pages)
            logger.info("MergePdfsTool: merged %d files → %d pages → %s",
                        len(file_paths), total_pages, out_path)
            return json.dumps({
                "status":      "merged",
                "file_path":   out_path,
                "filename":    output_filename,
                "pages":       total_pages,
                "files_merged": len(file_paths),
                "note":        "Pass file_path to upload_to_drive to save to Google Drive.",
            })

        except ImportError:
            return json.dumps({"error": "pypdf not installed. Run: pip install pypdf"})
        except Exception as exc:
            logger.exception("MergePdfsTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "file_paths":      {"type": "array", "items": {"type": "string"},
                                    "description": "List of PDF file paths to merge (in order)"},
                "output_filename": {"type": "string", "description": "Output filename (default: merged.pdf)"},
            }, "required": ["file_paths"]},
        }}
