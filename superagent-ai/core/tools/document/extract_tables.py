"""
ExtractTablesTool — extract tables from PDF or DOCX files.

Zone: GREEN — runs automatically, no human approval required.
"""
from __future__ import annotations
import csv
import io
import json
import logging
import os
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class ExtractTablesTool(BaseTool):
    """Extract tables from PDF or DOCX files as structured JSON or CSV.

    Input::

        {
            "file_path":   "/tmp/invoice.pdf",
            "format":      "json",          # "json" or "csv" (default: json)
            "page":        1                # specific page number for PDF (optional)
        }

    Returns::

        {
            "filename":   "invoice.pdf",
            "tables_found": 2,
            "tables": [
                {
                    "table_index": 0,
                    "page":        1,
                    "headers":     ["Item", "Qty", "Price", "Total"],
                    "rows":        [
                        ["Widget A", "5", "₹200", "₹1000"],
                        ["Widget B", "2", "₹500", "₹1000"]
                    ],
                    "csv":         "Item,Qty,Price,Total\\nWidget A,5,₹200,₹1000\\n..."
                }
            ]
        }
    """

    name: str = "extract_tables"
    description: str = (
        "Extract tables from PDF or DOCX files as structured JSON or CSV. GREEN — auto. "
        "Input JSON: {\"file_path\": \"/tmp/report.pdf\", \"format\": \"json\"}. "
        "Returns all tables with headers and rows. Pass csv output to export_csv."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, workspace_id: str | None = None) -> None:
        self._workspace_id = workspace_id

    def _extract_from_pdf(self, file_path: str, page_num: int | None) -> list:
        tables = []
        try:
            import pypdf
            reader = pypdf.PdfReader(file_path)
            pages  = [reader.pages[page_num - 1]] if page_num else reader.pages

            for p_idx, page in enumerate(pages, start=1):
                text = page.extract_text() or ""
                # Simple table detection: lines with consistent column separators
                lines = [l for l in text.split("\n") if l.strip()]
                table_rows = []
                for line in lines:
                    # Detect column separators: 2+ spaces or tabs
                    import re
                    cells = re.split(r"\s{2,}|\t", line.strip())
                    if len(cells) >= 2:
                        table_rows.append([c.strip() for c in cells])

                if len(table_rows) >= 2:
                    headers = table_rows[0]
                    rows    = table_rows[1:]
                    out     = io.StringIO()
                    writer  = csv.writer(out)
                    writer.writerow(headers)
                    writer.writerows(rows)
                    tables.append({
                        "table_index": len(tables),
                        "page":        p_idx if not page_num else page_num,
                        "headers":     headers,
                        "rows":        rows,
                        "csv":         out.getvalue(),
                    })
        except Exception as exc:
            logger.warning("PDF table extraction: %s", exc)
        return tables

    def _extract_from_docx(self, file_path: str) -> list:
        tables = []
        try:
            import docx
            doc = docx.Document(file_path)
            for t_idx, table in enumerate(doc.tables):
                rows = []
                for row in table.rows:
                    rows.append([cell.text.strip() for cell in row.cells])
                if not rows:
                    continue
                headers = rows[0]
                data    = rows[1:]
                out     = io.StringIO()
                writer  = csv.writer(out)
                writer.writerow(headers)
                writer.writerows(data)
                tables.append({
                    "table_index": t_idx,
                    "page":        None,
                    "headers":     headers,
                    "rows":        data,
                    "csv":         out.getvalue(),
                })
        except Exception as exc:
            logger.warning("DOCX table extraction: %s", exc)
        return tables

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        file_path = data.get("file_path", "")
        fmt       = data.get("format", "json")
        page_num  = data.get("page")

        if not file_path:
            return json.dumps({"error": "'file_path' is required."})
        if not os.path.exists(file_path):
            return json.dumps({"error": f"File not found: '{file_path}'"})

        ext = os.path.splitext(file_path)[1].lower()

        try:
            if ext == ".pdf":
                tables = self._extract_from_pdf(file_path, int(page_num) if page_num else None)
            elif ext == ".docx":
                tables = self._extract_from_docx(file_path)
            else:
                return json.dumps({"error": f"Unsupported file type '{ext}'. Supported: .pdf, .docx"})

            if not tables:
                return json.dumps({
                    "filename":     os.path.basename(file_path),
                    "tables_found": 0,
                    "tables":       [],
                    "note":         "No tables detected in this file.",
                })

            # If CSV format requested, drop JSON rows
            if fmt == "csv":
                for t in tables:
                    t.pop("rows", None)

            logger.info("ExtractTablesTool: %d tables from %s", len(tables), file_path)
            return json.dumps({
                "filename":     os.path.basename(file_path),
                "tables_found": len(tables),
                "tables":       tables,
            }, ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("ExtractTablesTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "file_path": {"type": "string", "description": "Path to PDF or DOCX file"},
                "format":    {"type": "string", "enum": ["json", "csv"], "description": "Output format"},
                "page":      {"type": "integer", "description": "Specific PDF page number (optional)"},
            }, "required": ["file_path"]},
        }}
