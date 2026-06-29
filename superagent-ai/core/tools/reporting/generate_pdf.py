"""
Generate PDF report tool — creates a formatted PDF summary using reportlab.

Zone: GREEN — runs automatically, no human approval required.

Writes the PDF to /tmp/<filename>.pdf and returns the path.
Supports weekly and monthly report formats.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class GeneratePDFTool(BaseTool):
    """Generate a formatted PDF report from structured data.

    Input format (JSON string)::

        {
            "title":       "Monthly Finance Report — March 2024",
            "period":      "monthly",           // "weekly" | "monthly"
            "sections":    [
                {
                    "heading": "Invoice Summary",
                    "content": "Total invoices: 12. Total amount: USD 45,200.00."
                },
                ...
            ],
            "summary":     "Overall financial health is good.",
            "filename":    "report_march_2024.pdf"   // optional
        }

    Returns:
        JSON dict with:
            ``status``    : "generated"
            ``file_path`` : absolute path to the PDF
            ``page_count``: int
            ``title``     : str
    """

    name: str = "generate_pdf"
    description: str = (
        "Generates a formatted PDF report using reportlab. "
        "Input JSON: {\"title\": \"...\", \"period\": \"monthly\", "
        "\"sections\": [{\"heading\": \"...\", \"content\": \"...\"}], "
        "\"summary\": \"...\", \"filename\": \"optional.pdf\"}. "
        "Returns JSON with file_path, page_count, title."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            params = self._parse_input(input_str)
            title = params.get("title", "Agent Report")
            period = params.get("period", "weekly")
            sections = params.get("sections", [])
            summary = params.get("summary", "")
            filename = params.get("filename") or self._default_filename(period)
            file_path = os.path.join("/tmp", filename)

            page_count = self._write_pdf(file_path, title, period, sections, summary)
            logger.info(f"GeneratePDFTool: wrote {page_count} page(s) to {file_path}")

            return json.dumps({
                "status": "generated",
                "file_path": file_path,
                "page_count": page_count,
                "title": title,
            })
        except Exception as exc:
            logger.error(f"GeneratePDFTool error: {exc}")
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
    def _default_filename(period: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"report_{period}_{ts}.pdf"

    @staticmethod
    def _write_pdf(
        file_path: str,
        title: str,
        period: str,
        sections: list[dict[str, Any]],
        summary: str,
    ) -> int:
        """Write a PDF using reportlab SimpleDocTemplate."""
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, HRFlowable, PageBreak
        )

        doc = SimpleDocTemplate(
            file_path,
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2.5 * cm,
            bottomMargin=2 * cm,
        )
        styles = getSampleStyleSheet()
        story: list[Any] = []

        # Title
        title_style = ParagraphStyle(
            "ReportTitle",
            parent=styles["Title"],
            fontSize=18,
            spaceAfter=6,
        )
        story.append(Paragraph(title, title_style))

        # Period + generated timestamp
        meta_style = ParagraphStyle(
            "Meta",
            parent=styles["Normal"],
            fontSize=9,
            textColor=colors.grey,
            spaceAfter=12,
        )
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        story.append(Paragraph(
            f"Period: {period.capitalize()} &nbsp;|&nbsp; Generated: {generated_at}",
            meta_style,
        ))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CCCCCC")))
        story.append(Spacer(1, 0.4 * cm))

        # Sections
        heading_style = ParagraphStyle(
            "SectionHeading",
            parent=styles["Heading2"],
            fontSize=13,
            spaceBefore=10,
            spaceAfter=4,
        )
        body_style = ParagraphStyle(
            "SectionBody",
            parent=styles["Normal"],
            fontSize=10,
            spaceAfter=8,
            leading=14,
        )
        for section in sections:
            heading = section.get("heading", "")
            content = section.get("content", "")
            if heading:
                story.append(Paragraph(heading, heading_style))
            if content:
                # Replace newlines with <br/> for reportlab
                content_html = content.replace("\n", "<br/>")
                story.append(Paragraph(content_html, body_style))
            story.append(Spacer(1, 0.2 * cm))

        # Summary
        if summary:
            story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CCCCCC")))
            story.append(Spacer(1, 0.3 * cm))
            summary_style = ParagraphStyle(
                "Summary",
                parent=styles["Normal"],
                fontSize=10,
                textColor=colors.HexColor("#333333"),
                leading=14,
            )
            story.append(Paragraph("<b>Summary</b>", heading_style))
            story.append(Paragraph(summary.replace("\n", "<br/>"), summary_style))

        doc.build(story)

        # Count pages by checking PDF byte marker
        with open(file_path, "rb") as f:
            content = f.read()
        page_count = content.count(b"/Page\n") or content.count(b"/Type /Page") or 1
        return page_count
