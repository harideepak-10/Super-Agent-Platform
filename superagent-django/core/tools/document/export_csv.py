"""
ExportCsvTool — export tabular data as a CSV file.

Zone: GREEN — runs automatically, no human approval required.

Accepts rows as a list of dicts or a list of lists.
Saves to the temp directory and returns the file path for upload_to_drive.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class ExportCsvTool(BaseTool):
    """Export tabular data to a CSV file.

    Input format (JSON string)::

        {
            "filename": "invoices_q2",     (optional — auto-generated if omitted)
            "headers":  ["Name", "Amount", "Date"],
            "rows": [
                ["Invoice #1042", "₹45,000", "2026-07-01"],
                ["Invoice #1043", "₹12,500", "2026-07-03"]
            ]
        }

    Or rows as list of dicts::

        {
            "filename": "tasks_report",
            "rows": [
                {"Task": "Send report", "Status": "completed", "Date": "2026-07-08"},
                ...
            ]
        }

    Returns::

        {
            "status":    "created",
            "filename":  "invoices_q2_20260708.csv",
            "file_path": "/tmp/krypsos_docs/invoices_q2_20260708.csv",
            "row_count": 2,
            "size_kb":   1.2
        }
    """

    name: str = "export_csv"
    description: str = (
        "Export tabular data to a CSV file. "
        "Input JSON: {\"filename\": \"...(optional)\", \"headers\": [...], \"rows\": [[...], ...]}. "
        "Rows can also be a list of dicts — headers are auto-extracted. "
        "Returns file_path. Pass it to upload_to_drive to save to Google Drive."
    )
    zone: ToolZone = ToolZone.GREEN

    _OUTPUT_DIR = os.path.join(tempfile.gettempdir(), "krypsos_docs")

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input. Expected JSON with rows."})

        rows     = data.get("rows", [])
        headers  = data.get("headers", [])
        filename = data.get("filename", "export")

        if not rows:
            return json.dumps({"error": "'rows' list is required."})

        try:
            return self._write_csv(filename, headers, rows)
        except Exception as exc:
            logger.exception("ExportCsvTool failed")
            return json.dumps({"error": str(exc)})

    def _write_csv(self, base_name: str, headers: list, rows: list) -> str:
        os.makedirs(self._OUTPUT_DIR, exist_ok=True)

        date_str  = datetime.now().strftime("%Y%m%d")
        safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in base_name)
        filename  = f"{safe_name}_{date_str}_{uuid.uuid4().hex[:6]}.csv"
        file_path = os.path.join(self._OUTPUT_DIR, filename)

        with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)

            # Auto-detect if rows are dicts
            if rows and isinstance(rows[0], dict):
                if not headers:
                    headers = list(rows[0].keys())
                writer.writerow(headers)
                for row in rows:
                    writer.writerow([row.get(h, "") for h in headers])
            else:
                if headers:
                    writer.writerow(headers)
                writer.writerows(rows)

        size_kb   = round(os.path.getsize(file_path) / 1024, 1)
        row_count = len(rows)
        logger.info("ExportCsvTool: created %s (%d rows, %.1f KB)", filename, row_count, size_kb)

        return json.dumps({
            "status":    "created",
            "filename":  filename,
            "file_path": file_path,
            "row_count": row_count,
            "size_kb":   size_kb,
            "format":    "csv",
        })

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Base filename (no extension)"},
                    "headers":  {"type": "array", "items": {"type": "string"}},
                    "rows":     {"type": "array", "items": {}, "description": "List of lists or list of dicts"},
                },
                "required": ["rows"],
            },
        }}
