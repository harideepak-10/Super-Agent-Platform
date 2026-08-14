"""
TranslateDocumentTool — translate document content to another language.

Zone: GREEN — runs automatically, no human approval required.

Supports:
- PDF
- DOCX
- TXT

Translation:
1. Google Cloud Translate
2. deep-translator fallback
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


_LANG_NAMES = {
    "en": "English",
    "ta": "Tamil",
    "hi": "Hindi",
    "ml": "Malayalam",
    "te": "Telugu",
    "kn": "Kannada",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "ar": "Arabic",
    "zh": "Chinese",
    "ja": "Japanese",
    "pt": "Portuguese",
    "ru": "Russian",
}


class TranslateDocumentTool(BaseTool):
    """Translate a document into another language."""

    name: str = "translate_document"

    description: str = (
        "Translate a PDF, DOCX, or TXT document to another language. "
        "GREEN — runs automatically. "
        'Input: {"file_path": "/tmp/report.pdf", "target_lang": "ta"}. '
        "Returns the translated document file_path. "
        "Do not create another document after this. "
        "If saving to Google Drive is requested, upload the returned file_path."
    )

    zone: ToolZone = ToolZone.GREEN

    def __init__(self, workspace_id: str | None = None) -> None:
        self._workspace_id = workspace_id

    def _read_text(self, file_path: str) -> str:
        """Extract text from PDF, DOCX, or TXT."""

        ext = os.path.splitext(file_path)[1].lower()

        # PDF
        if ext == ".pdf":
            try:
                import pypdf

                reader = pypdf.PdfReader(file_path)

                text = "\n".join(
                    page.extract_text() or ""
                    for page in reader.pages
                )

                if text.strip():
                    return text

            except Exception as exc:
                logger.exception("PDF extraction failed: %s", exc)

        # DOCX
        elif ext == ".docx":
            try:
                import docx

                document = docx.Document(file_path)

                text = "\n".join(
                    paragraph.text
                    for paragraph in document.paragraphs
                )

                if text.strip():
                    return text

            except Exception as exc:
                logger.exception("DOCX extraction failed: %s", exc)

        # TXT / fallback
        try:
            with open(
                file_path,
                "r",
                encoding="utf-8",
                errors="ignore",
            ) as file:
                return file.read()

        except Exception as exc:
            raise RuntimeError(
                f"Could not read document: {exc}"
            ) from exc

    def _translate_text(
        self,
        text: str,
        target_lang: str,
        source_lang: str | None,
    ) -> str:
        """Translate text using Google Cloud or deep-translator."""

        errors: list[str] = []

        # ---------------------------------------------------------
        # 1. Google Cloud Translate
        # ---------------------------------------------------------
        try:
            from google.cloud import translate_v2 as translate

            client = translate.Client()

            result = client.translate(
                text,
                target_language=target_lang,
                source_language=source_lang or None,
            )

            translated = result.get("translatedText")

            if translated and translated.strip():
                logger.info(
                    "Google Cloud translation successful: %s -> %s",
                    source_lang or "auto",
                    target_lang,
                )
                return translated

        except Exception as exc:
            errors.append(f"Google Cloud: {exc}")
            logger.warning(
                "Google Cloud Translate unavailable/failed: %s",
                exc,
            )

        # ---------------------------------------------------------
        # 2. deep-translator fallback
        # ---------------------------------------------------------
        try:
            from deep_translator import GoogleTranslator

            chunks = [
                text[i:i + 4500]
                for i in range(0, len(text), 4500)
            ]

            translator = GoogleTranslator(
                source=source_lang or "auto",
                target=target_lang,
            )

            translated_chunks: list[str] = []

            for chunk in chunks:
                result = translator.translate(chunk)

                if result:
                    translated_chunks.append(result)

            translated = "\n".join(translated_chunks)

            if translated.strip():
                logger.info(
                    "deep-translator successful: %s -> %s",
                    source_lang or "auto",
                    target_lang,
                )
                return translated

        except Exception as exc:
            errors.append(f"deep-translator: {exc}")
            logger.warning(
                "deep-translator failed: %s",
                exc,
            )

        raise RuntimeError(
            "Translation failed. "
            + " | ".join(errors)
        )

    def _save_output(
        self,
        text: str,
        file_path: str,
        target_lang: str,
        output_format: str,
    ) -> str:
        """Save translated text as DOCX or TXT."""

        base = os.path.splitext(
            os.path.basename(file_path)
        )[0]

        # ---------------------------------------------------------
        # DOCX
        # ---------------------------------------------------------
        if output_format == "docx":
            try:
                import docx

                output_path = os.path.join(
                    tempfile.gettempdir(),
                    f"{base}_{target_lang}.docx",
                )

                document = docx.Document()

                document.add_heading(
                    f"Translated Document "
                    f"({_LANG_NAMES.get(target_lang, target_lang)})",
                    0,
                )

                for line in text.splitlines():
                    if line.strip():
                        document.add_paragraph(line)

                document.save(output_path)

                if os.path.exists(output_path):
                    return output_path

                raise RuntimeError(
                    "DOCX file was not created."
                )

            except ImportError:
                logger.warning(
                    "python-docx is not installed. "
                    "Falling back to TXT."
                )

            except Exception as exc:
                raise RuntimeError(
                    f"Failed to create DOCX: {exc}"
                ) from exc

        # ---------------------------------------------------------
        # TXT fallback
        # ---------------------------------------------------------
        output_path = os.path.join(
            tempfile.gettempdir(),
            f"{base}_{target_lang}.txt",
        )

        with open(
            output_path,
            "w",
            encoding="utf-8",
        ) as file:
            file.write(text)

        if not os.path.exists(output_path):
            raise RuntimeError(
                "Translated TXT file was not created."
            )

        return output_path

    def run(self, input_str: str) -> str:
        """Execute document translation."""

        try:
            # -----------------------------------------------------
            # Parse input
            # -----------------------------------------------------
            if isinstance(input_str, str):
                data = json.loads(input_str)
            elif isinstance(input_str, dict):
                data = input_str
            else:
                return json.dumps({
                    "error": "Invalid input."
                })

        except (json.JSONDecodeError, TypeError) as exc:
            return json.dumps({
                "error": f"Invalid JSON input: {exc}"
            })

        # ---------------------------------------------------------
        # Input values
        # ---------------------------------------------------------
        file_path = data.get("file_path", "")
        target_lang = str(
            data.get("target_lang", "")
        ).lower().strip()

        source_lang = data.get("source_lang")
        if source_lang:
            source_lang = str(
                source_lang
            ).lower().strip()
        else:
            source_lang = None

        output_format = str(
            data.get("output_format", "docx")
        ).lower().strip()

        # ---------------------------------------------------------
        # Validation
        # ---------------------------------------------------------
        if not file_path:
            return json.dumps({
                "error": "'file_path' is required."
            })

        if not target_lang:
            return json.dumps({
                "error": (
                    "'target_lang' is required. "
                    f"Options: {list(_LANG_NAMES.keys())}"
                )
            })

        if target_lang not in _LANG_NAMES:
            return json.dumps({
                "error": (
                    f"Unsupported target language: {target_lang}. "
                    f"Options: {list(_LANG_NAMES.keys())}"
                )
            })

        if not os.path.isfile(file_path):
            return json.dumps({
                "error": f"File not found: '{file_path}'"
            })

        if output_format not in {"docx", "txt"}:
            output_format = "docx"

        # ---------------------------------------------------------
        # Translate
        # ---------------------------------------------------------
        try:
            logger.info(
                "Starting translation: file=%s target=%s source=%s",
                file_path,
                target_lang,
                source_lang or "auto",
            )

            text = self._read_text(file_path)

            if not text.strip():
                return json.dumps({
                    "error": (
                        "Could not extract text from file."
                    )
                })

            translated = self._translate_text(
                text,
                target_lang,
                source_lang,
            )

            if not translated.strip():
                return json.dumps({
                    "error": "Translation returned empty content."
                })

            # -----------------------------------------------------
            # Save translated document
            # -----------------------------------------------------
            output_path = self._save_output(
                translated,
                file_path,
                target_lang,
                output_format,
            )

            # Verify output exists
            if not os.path.isfile(output_path):
                return json.dumps({
                    "error": (
                        f"Translation completed but output file "
                        f"was not created: {output_path}"
                    )
                })

            word_count = len(translated.split())

            # -----------------------------------------------------
            # Breadcrumb
            # -----------------------------------------------------
            try:
                with open(
                    "/tmp/.krypsos_last_translation.json",
                    "w",
                    encoding="utf-8",
                ) as file:
                    json.dump(
                        {
                            "file_path": output_path,
                            "ts": time.time(),
                        },
                        file,
                    )
            except Exception:
                logger.warning(
                    "Could not write translation breadcrumb.",
                    exc_info=True,
                )

            logger.info(
                "Translation successful: %s -> %s | output=%s | words=%d",
                file_path,
                target_lang,
                output_path,
                word_count,
            )

            return json.dumps(
                {
                    "status": "translated",
                    "file_path": output_path,
                    "filename": os.path.basename(output_path),
                    "source_lang": source_lang or "auto",
                    "target_lang": target_lang,
                    "target_name": _LANG_NAMES.get(
                        target_lang,
                        target_lang,
                    ),
                    "word_count": word_count,
                    "note": (
                        "Pass this exact file_path to "
                        "upload_to_drive if the user wants "
                        "the translated document saved to Drive."
                    ),
                },
                ensure_ascii=False,
            )

        except Exception as exc:
            logger.exception(
                "TranslateDocumentTool failed"
            )

            return json.dumps({
                "error": str(exc),
                "status": "failed",
            })

    def to_schema(self) -> dict:
        """Return tool schema for the LLM."""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": (
                                "Path to PDF, DOCX, or TXT file."
                            ),
                        },
                        "target_lang": {
                            "type": "string",
                            "description": (
                                "Target language code: "
                                "ta, hi, ml, te, kn, fr, de, "
                                "es, ar, zh, ja, pt, ru."
                            ),
                        },
                        "source_lang": {
                            "type": "string",
                            "description": (
                                "Source language code. "
                                "Optional; auto-detected when omitted."
                            ),
                        },
                        "output_format": {
                            "type": "string",
                            "enum": ["docx", "txt"],
                            "description": (
                                "Output format. Defaults to docx."
                            ),
                        },
                    },
                    "required": [
                        "file_path",
                        "target_lang",
                    ],
                },
            },
        }