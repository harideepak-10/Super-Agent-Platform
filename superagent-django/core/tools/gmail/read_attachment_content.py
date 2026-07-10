"""
ReadAttachmentContentTool — read text content from a downloaded attachment.

Zone: GREEN — runs automatically, no human approval required.

Reads text from PDF, DOCX, CSV, or plain text files.
Pass the file_path returned by download_attachment.
"""
from __future__ import annotations
import json
import logging
import os
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)
_MAX_CHARS = 8000  # cap to avoid overwhelming the LLM context


class ReadAttachmentContentTool(BaseTool):
    """Read text content from a downloaded attachment file.

    Supports: PDF, DOCX, CSV, TXT, MD

    Input::

        {
            "file_path": "/tmp/krypsos_docs/invoice.pdf",
            "max_chars": 8000    (optional, default 8000)
        }

    Returns::

        {
            "filename":   "invoice.pdf",
            "file_type":  "pdf",
            "content":    "Invoice #1042\\nDate: July 1 2026\\nAmount: ₹45,000...",
            "char_count": 1240,
            "truncated":  false
        }
    """

    name: str = "read_attachment_content"
    description: str = (
        "Read the text content of a downloaded attachment (PDF, DOCX, CSV, TXT). "
        "Input JSON: {\"file_path\": \"...\", \"max_chars\": 8000}. "
        "Get file_path from download_attachment. "
        "Returns extracted text content ready to summarize or analyze."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        file_path = data.get("file_path", "")
        max_chars = int(data.get("max_chars", _MAX_CHARS))

        if not file_path:
            return json.dumps({"error": "'file_path' is required."})
        if not os.path.exists(file_path):
            return json.dumps({"error": f"File not found: {file_path}"})

        filename  = os.path.basename(file_path)
        ext       = os.path.splitext(filename)[1].lower()

        try:
            content = self._read_file(file_path, ext)
        except Exception as exc:
            logger.exception("ReadAttachmentContentTool failed for %s", filename)
            return json.dumps({"error": f"Could not read file: {exc}"})

        if not content or not content.strip():
            return json.dumps({
                "filename":  filename,
                "file_type": ext.lstrip("."),
                "error":     (
                    "No text could be extracted from this file. "
                    "It may be a scanned/image-based PDF that requires OCR, "
                    "a password-protected file, or an unsupported format."
                ),
                "content":   "",
                "char_count": 0,
            }, ensure_ascii=False)

        truncated = len(content) > max_chars
        content   = content[:max_chars]

        return json.dumps({
            "filename":   filename,
            "file_type":  ext.lstrip("."),
            "content":    content,
            "char_count": len(content),
            "truncated":  truncated,
        }, ensure_ascii=False)

    @staticmethod
    def _read_file(file_path: str, ext: str) -> str:
        if ext == ".pdf":
            return ReadAttachmentContentTool._read_pdf(file_path)
        elif ext == ".docx":
            return ReadAttachmentContentTool._read_docx(file_path)
        elif ext == ".csv":
            return ReadAttachmentContentTool._read_csv(file_path)
        elif ext in (".txt", ".md", ".log"):
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        else:
            # Try as plain text
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
            except Exception:
                return f"[Cannot read file type: {ext}]"

    @staticmethod
    def _read_pdf(file_path: str) -> str:
        # Try pdfminer.six first — handles embedded fonts and complex layouts
        # far better than pypdf. Fall back to pypdf if pdfminer isn't available.
        try:
            from pdfminer.high_level import extract_text as pdfminer_extract
            text = pdfminer_extract(file_path)
            if text and text.strip():
                return text.strip()
            # pdfminer returned blank — likely a scanned/image PDF, fall through
        except ImportError:
            pass
        except Exception:
            pass

        # Fallback: pypdf
        try:
            import pypdf
            reader = pypdf.PdfReader(file_path)
            pages  = [page.extract_text() or "" for page in reader.pages]
            text   = "\n\n".