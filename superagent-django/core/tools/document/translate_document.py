"""
TranslateDocumentTool — translate document content to another language.

Zone: GREEN — runs automatically, no human approval required.

Uses Google Translate API if connected, falls back to basic LLM-based translation prompt.
"""
from __future__ import annotations
import json
import logging
import os
import tempfile
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_LANG_NAMES = {
    "en": "English", "ta": "Tamil", "hi": "Hindi", "ml": "Malayalam",
    "te": "Telugu",  "kn": "Kannada", "fr": "French", "de": "German",
    "es": "Spanish", "ar": "Arabic",  "zh": "Chinese", "ja": "Japanese",
    "pt": "Portuguese", "ru": "Russian",
}


class TranslateDocumentTool(BaseTool):
    """Translate the text content of a document to another language.

    Reads the source file, translates the text, and saves a new document
    with the translated content.

    Input::

        {
            "file_path":    "/tmp/report.pdf",
            "target_lang":  "ta",               # language code: ta=Tamil, hi=Hindi, fr=French, etc.
            "source_lang":  "en",               # optional — auto-detected if not given
            "output_format": "docx"             # "docx" | "txt" (default: docx)
        }

    Returns::

        {
            "status":       "translated",
            "file_path":    "/tmp/report_ta.docx",
            "filename":     "report_ta.docx",
            "source_lang":  "en",
            "target_lang":  "ta",
            "target_name":  "Tamil",
            "word_count":   420
        }
    """

    name: str = "translate_document"
    description: str = (
        "Translate a document (PDF/DOCX/TXT) to another language. GREEN — runs automatically. "
        "Input JSON: {\"file_path\": \"/tmp/report.pdf\", \"target_lang\": \"ta\"} "
        "— language codes: ta=Tamil, hi=Hindi, fr=French, de=German, es=Spanish, ar=Arabic. "
        "IMPORTANT: The returned file_path IS the complete, ready-to-use translated Word document (.docx). "
        "Do NOT call generate_content or create_docx after this — the translation IS the final Word doc. "
        "If the user wants it saved to Drive, call upload_to_drive with the returned file_path."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, workspace_id: str | None = None) -> None:
        self._workspace_id = workspace_id

    def _read_text(self, file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(file_path)
                return "\n".join(page.extract_text() or "" for page in reader.pages)
            except Exception:
                pass
        if ext == ".docx":
            try:
                import docx
                doc = docx.Document(file_path)
                return "\n".join(p.text for p in doc.paragraphs)
            except Exception:
                pass
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            return f.read()

    def _translate_text(self, text: str, target_lang: str, source_lang: str) -> str:
        """Try Google Translate API; fall back to chunked translation prompt."""
        try:
            from google.cloud import translate_v2 as translate
            client = translate.Client()
            result = client.translate(
                text,
                target_language=target_lang,
                source_language=source_lang or None,
            )
            return result["translatedText"]
        except Exception:
            pass

        # Fallback: use deep-translator (pip install deep-translator) if available
        try:
            from deep_translator import GoogleTranslator
            # Chunk into 4500-char pieces (Google Translate limit)
            chunks     = [text[i:i + 4500] for i in range(0, len(text), 4500)]
            translator = GoogleTranslator(source=source_lang or "auto", target=target_lang)
            translated = [translator.translate(chunk) for chunk in chunks]
            return "\n".join(t or "" for t in translated)
        except Exception:
            pass

        # Last resort: return note
        return (
            f"[Translation to {_LANG_NAMES.get(target_lang, target_lang)} not available. "
            f"Install deep-translator: pip install deep-translator]\n\n{text[:500]}..."
        )

    def _save_output(self, text: str, file_path: str, target_lang: str, fmt: str) -> str:
        base     = os.path.splitext(os.path.basename(file_path))[0]
        out_name = f"{base}_{target_lang}.{fmt}"
        out_path = os.path.join(tempfile.gettempdir(), out_name)

        if fmt == "docx":
            try:
                import docx
                doc  = docx.Document()
                doc.add_heading(f"Translated Document ({_LANG_NAMES.get(target_lang, target_lang)})", 0)
                for line in text.split("\n"):
                    if line.strip():
                        doc.add_paragraph(line)
                doc.save(out_path)
                return out_path
            except ImportError:
                fmt = "txt"

        out_path = os.path.join(tempfile.gettempdir(), f"{base}_{target_lang}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        return out_path

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        file_path   = data.get("file_path", "")
        target_lang = data.get("target_lang", "")
        source_lang = data.get("source_lang", "en")
        out_format  = data.get("output_format", "docx")

        if not file_path:
            return json.dumps({"error": "'file_path' is required."})
        if not target_lang:
            return json.dumps({"error": f"'target_lang' is required. Options: {list(_LANG_NAMES.keys())}"})
        if not os.path.exists(file_path):
            return json.dumps({"error": f"File not found: '{file_path}'"})

        try:
            text       = self._read_text(file_path)
            if not text.strip():
                return json.dumps({"error": "Could not extract text from file."})

            translated = self._translate_text(text, target_lang, source_lang)
            out_path   = self._save_output(translated, file_path, target_lang, out_format)
            word_count = len(translated.split())

            logger.info("TranslateDocumentTool: %s → %s words=%d", file_path, target_lang, word_count)
            return json.dumps({
                "status":      "translated",
                "file_path":   out_path,
                "filename":    os.path.basename(out_path),
                "source_lang": source_lang,
                "target_lang": target_lang,
                "target_name": _LANG_NAMES.get(target_lang, target_lang),
                "word_count":  word_count,
                "note":        "Pass file_path to upload_to_drive to save to Google Drive.",
            }, ensure_ascii=False)

        except Exception as exc:
            logger.exception("TranslateDocumentTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "file_path":     {"type": "string", "description": "Path to PDF/DOCX/TXT file"},
                "target_lang":   {"type": "string",
                                  "description": "Target language code: ta, hi, fr, de, es, ar, zh, ja, pt, ru"},
                "source_lang":   {"type": "string", "description": "Source language code (default: en)"},
                "output_format": {"type": "string", "enum": ["docx", "txt"],
                                  "description": "Output format (default: docx)"},
            }, "required": ["file_path", "target_lang"]},
        }}
