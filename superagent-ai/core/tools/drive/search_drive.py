"""
Search Google Drive tool — queries Drive for files by name, type, or content.

Zone: GREEN — runs automatically, no human approval required.

Accepts an optional ``drive_service`` so tests can inject MockDriveService.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 20


class SearchDriveTool(BaseTool):
    """Search Google Drive and return a list of matching files.

    Input format (JSON string)::

        {"query": "invoice 2024", "limit": 20, "file_type": "pdf"}

    ``file_type`` is optional — when given, appends a mimeType filter.
    Common values: ``pdf``, ``docx``, ``xlsx``, ``sheet``, ``doc``.

    Returns:
        JSON list of file dicts, each with:
        ``id``, ``name``, ``mimeType``, ``size``, ``modifiedTime``,
        ``webViewLink``, ``parents``.
    """

    name: str = "search_drive"
    description: str = (
        "Search Google Drive for files by name or content. "
        "Input JSON: {\"query\": \"invoice 2024\", \"limit\": 20, \"file_type\": \"pdf\"}. "
        "Returns a JSON list of files with id, name, mimeType, size, modifiedTime, webViewLink."
    )
    zone: ToolZone = ToolZone.GREEN

    _MIME_MAP: dict[str, str] = {
        "pdf":   "application/pdf",
        "docx":  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx":  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "sheet": "application/vnd.google-apps.spreadsheet",
        "doc":   "application/vnd.google-apps.document",
        "folder":"application/vnd.google-apps.folder",
    }

    def __init__(self, drive_service: Any = None) -> None:
        self._injected_service = drive_service
        self._service: Any = None

    def run(self, input_str: str) -> str:
        query, limit, file_type = self._parse_input(input_str)

        try:
            service = self._get_service()
            return self._search(service, query, limit, file_type)
        except Exception as exc:
            error_msg = f"Drive API error while searching: {exc}"
            logger.error(error_msg)
            return json.dumps({"error": error_msg, "files": []})

    def _get_service(self) -> Any:
        if self._injected_service is not None:
            return self._injected_service
        if self._service is None:
            from core.tools.drive.auth import DriveAuth
            self._service = DriveAuth().build_service("default")
        return self._service

    @staticmethod
    def _parse_input(input_str: str) -> tuple[str, int, str]:
        if input_str and input_str.strip().startswith("{"):
            try:
                data = json.loads(input_str)
                query = str(data.get("query", ""))
                limit = int(data.get("limit", _DEFAULT_LIMIT))
                file_type = str(data.get("file_type", "")).lower()
                return query, limit, file_type
            except (json.JSONDecodeError, ValueError):
                pass
        return input_str.strip(), _DEFAULT_LIMIT, ""

    def _search(self, service: Any, query: str, limit: int, file_type: str) -> str:
        q_parts = []
        if query:
            q_parts.append(f"name contains '{query}'")
        if file_type and file_type in self._MIME_MAP:
            q_parts.append(f"mimeType='{self._MIME_MAP[file_type]}'")
        q_parts.append("trashed=false")

        q_str = " and ".join(q_parts)

        result = (
            service.files()
            .list(
                q=q_str,
                pageSize=limit,
                fields="files(id, name, mimeType, size, modifiedTime, webViewLink, parents)",
            )
            .execute()
        )

        files = result.get("files", [])
        return json.dumps(files, ensure_ascii=False)
