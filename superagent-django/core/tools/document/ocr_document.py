"""
OcrDocumentTool — extract text from scanned/image PDFs using OCR.

Zone: GREEN — runs automatically, no human approval required.

Uses pytesseract (Tesseract OCR) if available, falls back to pypdf text extraction.
"""
from __future__ import annotations
import json
import logging
import os
import tempfile
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class OcrDocumentTool(BaseTool):
    """Extract text from scanned PDFs or image files using OCR.

    Use this when read_attachment_content or summarize_document returns empty text
    (indicating a scanned/image-based PDF rather than a text PDF).

    Input::

        {
            "file_path": "/tmp/scanned_invoice.pdf",
            "language":  "eng",            # Tesseract language code (default: eng)
            "pages":     [1, 2]            # specific pages to OCR (default: all)
        }

    Returns::

        {
            "filename":    "scanned_invoice.pdf",
            "pages_ocrd":  3,
            "text":        "Full extracted text...",
            "word_count":  420,
            "method":      "tesseract"     # or "pypdf_fallback"
        }
    """

    name: str = "ocr_document"
    description: str = (
        "Extract text from scanned PDFs or image files using OCR. GREEN — auto. "
        "Input JSON: {\"file_path\": \"/tmp/scanned.pdf\", \"language\": \"eng\"}. "
        "Use when read_attachment_content returns empty text (scanned document). "
        "Returns full extracted text."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, workspace_id: str | None = None) -> None:
        self._workspace_id = workspace_id

    def _ocr_with_tesseract(self, file_path: str, language: str, pages: list) -> dict:
        import pypdf
        from PIL import Image
        import pytesseract

        reader    = pypdf.PdfReader(file_path)
        all_pages = list(range(len(reader.pages)))
        target    = [p - 1 for p in pages] if pages else all_pages

        texts = []
        for p_idx in target:
            if p_idx >= len(reader.pages):
                continue
            page = reader.pages[p_idx]
            # Convert page to image
            if hasattr(page, "to_image"):
                # pdfplumber style
                img = page.to_image(resolution=200).original
            else:
                # Use pdf2image if available
                try:
                    from pdf2image import convert_from_path
                    images = convert_from_path(file_path, first_page=p_idx + 1,
                                               last_page=p_idx + 1, dpi=200)
                    img = images[0] if images else None
                except ImportError:
                    img = None

            if img:
                text = pytesseract.image_to_string(img, lang=language)
                texts.append(text)

        full_text = "\n\n".join(texts)
        return {
            "text":       full_text,
            "pages_ocrd": len(texts),
            "method":     "tesseract",
        }

    def _ocr_pypdf_fallback(self, file_path: str, pages: list) -> dict:
        import pypdf
        reader    = pypdf.PdfReader(file_path)
        all_pages = list(range(len(reader.pages)))
        target    = [p - 1 for p in pages] if pages else all_pages

        texts = []
        for p_idx in target:
            if p_idx >= len(reader.pages):
                continue
            text = reader.pages[p_idx].extract_text() or ""
            if text.strip():
                texts.append(text)

        return {
            "text":       "\n\n".join(texts),
            "pages_ocrd": len(texts),
            "method":     "pypdf_fallback",
        }

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        file_path = data.get("file_path", "")
        language  = data.get("language", "eng")
        pages     = data.get("pages", [])

        if not file_path:
            return json.dumps({"error": "'file_path' is required."})
        if not os.path.exists(file_path):
            return json.dumps({"error": f"File not found: '{file_path}'"})

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in (".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif"):
            return json.dumps({"error": f"Unsupported file type '{ext}'. Supported: .pdf, .png, .jpg, .tiff"})

        try:
            # Try Tesseract first
            try:
                import pytesseract
                from PIL import Image
                result = self._ocr_with_tesseract(file_path, language, pages)
            except (ImportError, Exception) as e:
                logger.warning("Tesseract not available (%s), falling back to pypdf", e)
                result = self._ocr_pypdf_fallback(file_path, pages)

            text       = result["text"]
            word_count = len(text.split())

            logger.info("OcrDocumentTool: %s pages=%d words=%d method=%s",
                        file_path, result["pages_ocrd"], word_count, result["method"])

            return json.dumps({
                "filename":    os.path.basename(file_path),
                "pages_ocrd":  result["pages_ocrd"],
                "text":        text[:10000],   # cap at 10k chars
                "word_count":  word_count,
                "method":      result["method"],
                "truncated":   len(text) > 10000,
            }, ensure_ascii=False, default=str)

        except Exception as exc:
            logger.exception("OcrDocumentTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "file_path": {"type": "string", "description": "Path to scanned PDF or image file"},
                "language":  {"type": "string", "description": "Tesseract language code (default: eng)"},
                "pages":     {"type": "array", "items": {"type": "integer"},
                              "description": "Specific page numbers to OCR (default: all)"},
            }, "required": ["file_path"]},
        }}
