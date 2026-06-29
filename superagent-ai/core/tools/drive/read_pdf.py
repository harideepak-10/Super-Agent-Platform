"""
Read PDF tool — downloads a file from Drive and extracts text using PyPDF2.

Zone: GREEN — runs automatically, no human approval required.

The PDF bytes are fetched from Drive (or injected via MockDriveService),
then parsed locally by PyPDF2.  No cloud OCR service is called.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class ReadPDFTool(BaseTool):
    """Download a PDF from Google Drive and extract its text.

    Input format (JSON string or plain file ID)::

        {"file_id": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"}
        or just the raw file ID string.

    Returns:
        JSON string with keys:
            ``file_id``   : str
            ``page_count``: int
            ``text``      : str  — full extracted plain text
            ``pages``     : list[str]  — per-page text list
    """

    name: str = "read_pdf"
    description: str = (
        "Downloads a PDF from Google Drive and extracts all text using PyPDF2. "
        "Input JSON: {\"file_id\": \"<drive_file_id>\"}. "
        "Returns JSON with file_id, page_count, text (full), and pages (per-page list)."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, drive_service: Any = None) -> None:
        self._injected_service = drive_service
        self._service: Any = None

    def run(self, input_str: str) -> str:
        file_id = self._parse_input(input_str)
        if not file_id:
            return json.dumps({"error": "file_id is required", "text": ""})

        try:
            service = self._get_service()
            pdf_bytes = self._download(service, file_id)
            return self._extract_text(file_id, pdf_bytes)
        except Exception as exc:
            error_msg = f"ReadPDFTool error for file {file_id!r}: {exc}"
            logger.error(error_msg)
            return json.dumps({"error": error_msg, "file_id": file_id, "text": ""})

    def _get_service(self) -> Any:
        if self._injected_service is not None:
            return self._injected_service
        if self._service is None:
            from core.tools.drive.auth import DriveAuth
            self._service = DriveAuth().build_service("default")
        return self._service

    @staticmethod
    def _parse_input(input_str: str) -> str:
        if not input_str:
            return ""
        s = input_str.strip()
        if s.startswith("{"):
            try:
                data = json.loads(s)
                return str(data.get("file_id", "")).strip()
            except json.JSONDecodeError:
                pass
        return s

    @staticmethod
    def _download(service: Any, file_id: str) -> bytes:
        """Download raw bytes from Drive (real service or mock)."""
        # Support mock services that return bytes directly
        if hasattr(service, "get_pdf_bytes"):
            return service.get_pdf_bytes(file_id)

        request = service.files().get_media(fileId=file_id)
        buffer = io.BytesIO()

        # Use MediaIoBaseDownload if available (real API), else execute()
        try:
            from googleapiclient.http import MediaIoBaseDownload  # type: ignore[import]
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return buffer.getvalue()
        except ImportError:
            return request.execute()

    @staticmethod
    def _extract_text(file_id: str, pdf_bytes: bytes) -> str:
        """Extract text from PDF bytes using PyPDF2."""
        try:
            import PyPDF2  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "PyPDF2 is not installed. Run: pip install PyPDF2"
            ) from exc

        reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        pages: list[str] = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")

        full_text = "\n\n".join(pages)

        return json.dumps({
            "file_id": file_id,
            "page_count": len(pages),
            "text": full_text,
            "pages": pages,
        }, ensure_ascii=False)
