"""
Export report tool — saves a structured report as JSON or plain text.

Zone: GREEN — runs automatically, no human approval required.

Complements generate_pdf by providing a machine-readable export
alongside the PDF for downstream systems or further processing.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class ExportReportTool(BaseTool):
    """Export a report as a JSON or plain-text file in /tmp/.

    Input format (JSON string)::

        {
            "title":    "Weekly Summary",
            "period":   "weekly",
            "sections": [{"heading": "...", "content": "..."}],
            "summary":  "...",
            "format":   "json",          // "json" | "text" (default: json)
            "filename": "report.json"    // optional
        }

    Returns:
        JSON dict with:
            ``status``   : "exported"
            ``file_path``: absolute path
            ``format``   : "json" | "text"
            ``title``    : str
    """

    name: str = "export_report"
    description: str = (
        "Saves a report as a JSON or plain-text file in /tmp/. "
        "Input JSON: {\"title\": \"...\", \"sections\": [...], "
        "\"format\": \"json\", \"filename\": \"optional\"}. "
        "Returns JSON with file_path, format, title."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            params = self._parse_input(input_str)
            title = params.get("title", "Report")
            period = params.get("period", "")
            sections = params.get("sections", [])
            summary = params.get("summary", "")
            fmt = params.get("format", "json").lower()
            filename = params.get("filename") or self._default_filename(period, fmt)
            file_path = os.path.join("/tmp", filename)

            if fmt == "text":
                self._write_text(file_path, title, period, sections, summary)
            else:
                self._write_json(file_path, title, period, sections, summary)

            logger.info(f"ExportReportTool: wrote {fmt} report to {file_path}")
            return json.dumps({
                "status": "exported",
                "file_path": file_path,
                "format": fmt,
                "title": title,
            })
        except Exception as exc:
            logger.error(f"ExportReportTool error: {exc}")
            return json.dumps({"error": str(exc)})

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
        return {"title": s}

    @staticmethod
    def _default_filename(period: str, fmt: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        ext = "txt" if fmt == "text" else "json"
        label = f"_{period}" if period else ""
        return f"report{label}_{ts}.{ext}"

    @staticmethod
    def _write_json(file_path: str, title: str, period: str,
                    sections: list[dict], summary: str) -> None:
        data = {
            "title": title,
            "period": period,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sections": sections,
            "summary": summary,
        }
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _write_text(file_path: str, title: str, period: str,
                    sections: list[dict], summary: str) -> None:
        lines = [
            title,
            "=" * len(title),
            f"Period: {period}" if period else "",
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
        ]
        for section in sections:
            heading = section.get("heading", "")
            content = section.get("content", "")
            if heading:
                lines += [heading, "-" * len(heading)]
            if content:
                lines.append(content)
            lines.append("")
        if summary:
            lines += ["Summary", "-------", summary]
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
