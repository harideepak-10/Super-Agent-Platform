"""
CompareDocumentsTool — diff two document versions and highlight changes.

Zone: GREEN — runs automatically, no human approval required.
"""
from __future__ import annotations
import difflib
import json
import logging
import os
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class CompareDocumentsTool(BaseTool):
    """Compare two document versions and return a summary of changes.

    Supports PDF, DOCX, and TXT files.

    Input::

        {
            "file_path_a": "/tmp/contract_v1.docx",
            "file_path_b": "/tmp/contract_v2.docx",
            "mode":        "summary"          # "summary" or "full_diff" (default: summary)
        }

    Returns::

        {
            "file_a":         "contract_v1.docx",
            "file_b":         "contract_v2.docx",
            "lines_added":    12,
            "lines_removed":  5,
            "lines_unchanged":148,
            "similarity":     0.94,
            "changes_summary": "12 lines added, 5 lines removed. Documents are 94% similar.",
            "added_lines":    ["+ New clause 7: payment terms..."],
            "removed_lines":  ["- Old clause 7: net 30 days..."],
            "diff_preview":   "--- contract_v1.docx\n+++ contract_v2.docx\n..."
        }
    """

    name: str = "compare_documents"
    description: str = (
        "Compare two document versions and show what changed. GREEN — runs automatically. "
        "Input JSON: {\"file_path_a\": \"/tmp/v1.docx\", \"file_path_b\": \"/tmp/v2.docx\", "
        "\"mode\": \"summary\"}. "
        "Returns added/removed lines and similarity score."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, workspace_id: str | None = None) -> None:
        self._workspace_id = workspace_id

    def _extract_text(self, file_path: str) -> list[str]:
        """Extract text lines from PDF, DOCX, or TXT."""
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".docx":
            try:
                import docx
                doc = docx.Document(file_path)
                return [p.text for p in doc.paragraphs if p.text.strip()]
            except ImportError:
                pass

        if ext == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(file_path)
                lines  = []
                for page in reader.pages:
                    text = page.extract_text() or ""
                    lines.extend(text.split("\n"))
                return [l for l in lines if l.strip()]
            except ImportError:
                pass

        # Fallback: plain text
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                return [l.rstrip("\n") for l in f if l.strip()]
        except Exception as exc:
            raise RuntimeError(f"Cannot read file: {exc}")

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        path_a = data.get("file_path_a", "")
        path_b = data.get("file_path_b", "")
        mode   = data.get("mode", "summary")

        if not path_a or not path_b:
            return json.dumps({"error": "'file_path_a' and 'file_path_b' are both required."})
        for p in (path_a, path_b):
            if not os.path.exists(p):
                return json.dumps({"error": f"File not found: '{p}'"})

        try:
            lines_a = self._extract_text(path_a)
            lines_b = self._extract_text(path_b)

            diff = list(difflib.unified_diff(
                lines_a, lines_b,
                fromfile=os.path.basename(path_a),
                tofile=os.path.basename(path_b),
                lineterm="",
            ))

            added     = [l for l in diff if l.startswith("+") and not l.startswith("+++")]
            removed   = [l for l in diff if l.startswith("-") and not l.startswith("---")]
            unchanged = len(lines_a) - len(removed)

            matcher    = difflib.SequenceMatcher(None, lines_a, lines_b)
            similarity = round(matcher.ratio(), 4)

            diff_preview = "\n".join(diff[:80])  # first 80 diff lines

            changes_summary = (
                f"{len(added)} line(s) added, {len(removed)} line(s) removed. "
                f"Documents are {similarity * 100:.1f}% similar."
            )

            result = {
                "file_a":           os.path.basename(path_a),
                "file_b":           os.path.basename(path_b),
                "lines_added":      len(added),
                "lines_removed":    len(removed),
                "lines_unchanged":  max(unchanged, 0),
                "similarity":       similarity,
                "changes_summary":  changes_summary,
                "added_lines":      added[:20],
                "removed_lines":    removed[:20],
            }

            if mode == "full_diff":
                result["diff_preview"] = diff_preview

            logger.info("CompareDocumentsTool: %s vs %s → similarity=%.2f",
                        path_a, path_b, similarity)
            return json.dumps(result, ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("CompareDocumentsTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "file_path_a": {"type": "string", "description": "Path to first (original) document"},
                "file_path_b": {"type": "string", "description": "Path to second (updated) document"},
                "mode":        {"type": "string", "enum": ["summary", "full_diff"],
                                "description": "'summary' for overview, 'full_diff' for full diff text"},
            }, "required": ["file_path_a", "file_path_b"]},
        }}
