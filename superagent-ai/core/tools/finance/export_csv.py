"""
Export CSV tool — exports invoice data to a CSV file in /tmp/.

Zone: GREEN — runs automatically, no human approval required.

The file is written to /tmp/invoices_<timestamp>.csv and the path
is returned in the result so the caller can retrieve it.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_CSV_FIELDS = [
    "id",
    "invoice_number",
    "vendor_name",
    "vendor_email",
    "amount",
    "currency",
    "invoice_date",
    "due_date",
    "status",
]


class ExportCSVTool(BaseTool):
    """Export a list of invoices to a CSV file in /tmp/.

    Input format (JSON string)::

        {
            "invoices": [...],            // list of invoice dicts
            "filename": "my_export.csv"   // optional custom filename
        }

    Returns:
        JSON dict with:
            ``status``    : "exported"
            ``file_path`` : absolute path to the CSV file
            ``row_count`` : int
            ``fields``    : list of column headers written
    """

    name: str = "export_csv"
    description: str = (
        "Exports a list of invoices to a CSV file in /tmp/. "
        "Input JSON: {\"invoices\": [...], \"filename\": \"optional.csv\"}. "
        "Returns JSON with file_path, row_count, fields."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            params = self._parse_input(input_str)
            invoices: list[dict[str, Any]] = params.get("invoices", [])
            if not invoices:
                return json.dumps({"error": "No invoices provided to export.", "row_count": 0})

            filename = params.get("filename") or self._default_filename()
            file_path = os.path.join("/tmp", filename)

            row_count = self._write_csv(file_path, invoices)
            logger.info(f"ExportCSVTool: wrote {row_count} rows to {file_path}")

            return json.dumps({
                "status": "exported",
                "file_path": file_path,
                "row_count": row_count,
                "fields": _CSV_FIELDS,
            })
        except Exception as exc:
            logger.error(f"ExportCSVTool error: {exc}")
            return json.dumps({"error": str(exc), "row_count": 0})

    @staticmethod
    def _parse_input(input_str: str) -> dict[str, Any]:
        if not input_str or not input_str.strip():
            return {}
        s = input_str.strip()
        if s.startswith("{"):
            try:
                return json.loads(s)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON: {exc}") from exc
        raise ValueError("ExportCSVTool expects a JSON string.")

    @staticmethod
    def _default_filename() -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"invoices_{ts}.csv"

    @staticmethod
    def _write_csv(file_path: str, invoices: list[dict[str, Any]]) -> int:
        """Write invoices to a CSV file, returning the number of data rows."""
        # Discover all unique fields (standard + any extras)
        extra_fields = []
        for inv in invoices:
            for k in inv:
                if k not in _CSV_FIELDS and k not in extra_fields:
                    extra_fields.append(k)
        all_fields = _CSV_FIELDS + extra_fields

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=all_fields, extrasaction="ignore"
            )
            writer.writeheader()
            for inv in invoices:
                # Flatten line_items to a string if present
                row = dict(inv)
                if "line_items" in row and isinstance(row["line_items"], list):
                    row["line_items"] = json.dumps(row["line_items"])
                writer.writerow(row)

        return len(invoices)
