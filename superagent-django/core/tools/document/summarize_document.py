"""
SummarizeDocumentTool — summarize a long document into key points.

Zone: GREEN — runs automatically, no human approval required.
"""
from __future__ import annotations
import json
import logging
import os
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class SummarizeDocumentTool(BaseTool):
    """Summarize a PDF, DOCX, TXT, or CSV file into key points.

    Reads the file, extracts text, then produces a structured summary
    with overview, key points, and action items.

    Input::

        {
            "file_path":   "/tmp/Q3_Report.pdf",
            "max_points":  10,                    # max bullet points (default 10)
            "focus":       "financial figures"    # optional — what to focus on
        }

    Returns::

        {
            "filename":    "Q3_Report.pdf",
            "word_count":  3420,
            "overview":    "This report covers Q3 2026 financial performance...",
            "key_points":  [
                "Revenue grew 18% YoY to ₹4.2Cr",
                "Operating costs reduced by 12%",
                ...
            ],
            "action_items": ["Follow up on unpaid invoices", ...],
            "summary_text": "Full formatted summary..."
        }
    """

    name: str = "summarize_document"
    description: str = (
        "Summarize a PDF, DOCX, TXT, or CSV file into key points. GREEN — runs automatically. "
        "Input JSON: {\"file_path\": \"/tmp/report.pdf\", \"max_points\": 10, "
        "\"focus\": \"financial figures (optional)\"}. "
        "Use read_from_drive or download_attachment to get the file first."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, workspace_id: str | None = None) -> None:
        self._workspace_id = workspace_id

    def _read_file_text(self, file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(file_path)
                return "\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError:
                return self._read_raw(file_path)

        if ext == ".docx":
            try:
                import docx
                doc  = docx.Document(file_path)
                return "\n".join(p.text for p in doc.paragraphs)
            except ImportError:
                return self._read_raw(file_path)

        if ext == ".csv":
            try:
                import csv
                rows = []
                with open(file_path, newline="", encoding="utf-8", errors="ignore") as f:
                    reader = csv.reader(f)
                    for row in reader:
                        rows.append(", ".join(row))
                return "\n".join(rows[:200])  # first 200 rows
            except Exception:
                return self._read_raw(file_path)

        return self._read_raw(file_path)

    def _read_raw(self, file_path: str) -> str:
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as exc:
            raise RuntimeError(f"Cannot read file: {exc}")

    def _extract_key_points(self, text: str, max_points: int, focus: str) -> dict:
        """Simple rule-based extractor — works without an LLM call."""
        import re

        lines     = [l.strip() for l in text.split("\n") if l.strip()]
        sentences = re.split(r"(?<=[.!?])\s+", text)

        # Score sentences by relevance
        focus_words = set(focus.lower().split()) if focus else set()
        scored = []
        for s in sentences:
            s = s.strip()
            if len(s) < 20:
                continue
            score = 0
            sl = s.lower()
            # Boost sentences with numbers, currency, percentages
            if re.search(r"\d+[%₹$€]|\d+\.\d+|₹[\d,]+", s):
                score += 3
            # Boost focus words
            for w in focus_words:
                if w in sl:
                    score += 2
            # Boost action verbs
            for w in ["increase", "decrease", "grow", "revenue", "profit", "loss",
                       "recommend", "action", "key", "important", "critical", "must"]:
                if w in sl:
                    score += 1
            scored.append((score, s))

        scored.sort(key=lambda x: -x[0])
        # Truncate each key point to 200 chars to keep token count low
        key_points = [s[:200] for _, s in scored[:max_points]]

        # Simple overview — first 2 non-trivial sentences, capped at 400 chars
        overview_sents = [s for s in sentences if len(s) > 40][:2]
        overview = " ".join(overview_sents)[:400]

        # Action items — sentences with action words, each capped at 150 chars
        action_items = []
        for s in sentences:
            sl = s.lower()
            if any(w in sl for w in ["follow up", "send", "review", "approve", "confirm",
                                      "schedule", "contact", "submit", "complete", "action"]):
                if len(s) > 20:
                    action_items.append(s.strip()[:150])
        action_items = action_items[:5]

        summary_text = (
            f"**Overview:**\n{overview}\n\n"
            f"**Key Points:**\n" + "\n".join(f"• {p}" for p in key_points) +
            (f"\n\n**Action Items:**\n" + "\n".join(f"• {a}" for a in action_items) if action_items else "")
        )
        # Hard cap: keep summary_text under ~1500 tokens (~6000 chars)
        if len(summary_text) > 6000:
            summary_text = summary_text[:6000] + "\n\n[truncated]"

        return {
            "overview":     overview,
            "key_points":   key_points,
            "action_items": action_items,
            "summary_text": summary_text,
        }

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        file_path  = data.get("file_path", "")
        max_points = int(data.get("max_points", 10))
        focus      = data.get("focus", "")

        if not file_path:
            return json.dumps({"error": "'file_path' is required."})
        if not os.path.exists(file_path):
            return json.dumps({"error": f"File not found: '{file_path}'"})

        try:
            text = self._read_file_text(file_path)
            if not text.strip():
                return json.dumps({"error": "Could not extract text from file — it may be a scanned image. Try ocr_document instead."})

            word_count = len(text.split())
            extracted  = self._extract_key_points(text, max_points, focus)
            filename   = os.path.basename(file_path)

            logger.info("SummarizeDocumentTool: summarized %s words=%d", filename, word_count)
            return json.dumps({
                "filename":    filename,
                "word_count":  word_count,
                **extracted,
            }, ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("SummarizeDocumentTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "file_path":  {"type": "string", "description": "Local path to PDF/DOCX/TXT/CSV file"},
                "max_points": {"type": "integer", "description": "Max key points to extract (default 10)"},
                "focus":      {"type": "string",  "description": "What to focus on e.g. 'financial figures'"},
            }, "required": ["file_path"]},
        }}
