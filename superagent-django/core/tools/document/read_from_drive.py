"""
ReadFromDriveTool — list and download files from Google Drive.

Zone: GREEN — runs automatically, no human approval required.
"""
from __future__ import annotations
import json
import logging
import os
import tempfile
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_MIME_LABELS = {
    "application/vnd.google-apps.document":     "Google Doc",
    "application/vnd.google-apps.spreadsheet":  "Google Sheet",
    "application/vnd.google-apps.presentation": "Google Slides",
    "application/pdf":                          "PDF",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word (.docx)",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":       "Excel (.xlsx)",
    "text/plain":                               "Text",
    "text/csv":                                 "CSV",
}

_EXPORT_MAP = {
    "application/vnd.google-apps.document":     ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet":  ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
}


class ReadFromDriveTool(BaseTool):
    """List and optionally download files from Google Drive.

    --- List files ---
    Input::

        {
            "action":      "list",
            "folder_name": "KRYPSOS Reports",    # optional — filter by folder
            "query":       "invoice",             # optional — search in filename
            "max_results": 20                     # default: 20
        }

    --- Download a file ---
    Input::

        {
            "action":  "download",
            "file_id": "1BxiM...",               # Drive file ID from list result
            "filename": "report.pdf"              # optional — override filename
        }

    Returns (list)::

        {
            "files": [
                {
                    "file_id":   "1BxiM...",
                    "name":      "Q3 Report.pdf",
                    "type":      "PDF",
                    "size_kb":   142,
                    "modified":  "2026-07-01T10:00:00Z",
                    "web_url":   "https://drive.google.com/..."
                }
            ],
            "total": 5
        }

    Returns (download)::

        {
            "status":    "downloaded",
            "file_path": "/tmp/Q3_Report.pdf",
            "filename":  "Q3_Report.pdf",
            "size_kb":   142
        }
    """

    name: str = "read_from_drive"
    description: str = (
        "List or download files from Google Drive. GREEN — runs automatically. "
        "List: {\"action\": \"list\", \"folder_name\": \"Reports\", \"query\": \"invoice\"}. "
        "Download: {\"action\": \"download\", \"file_id\": \"...\"}. "
        "After downloading, pass file_path to read_attachment_content or summarize_document."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, workspace_id: str | None = None, drive_service: Any = None) -> None:
        self._workspace_id     = workspace_id
        self._injected_service = drive_service

    def _get_service(self) -> Any:
        if self._injected_service:
            return self._injected_service
        if not self._workspace_id:
            raise RuntimeError("No workspace_id provided.")
        from apps.integrations.models import Integration
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        integration = Integration.objects.filter(
            workspace_id=self._workspace_id,
            provider=Integration.Provider.GOOGLE_DRIVE,
            status=Integration.Status.ACTIVE,
        ).first()
        if not integration or not integration.access_token:
            raise RuntimeError("Google Drive not connected.")
        creds = Credentials(
            token=integration.access_token,
            refresh_token=integration.refresh_token,
            client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            token_uri="https://oauth2.googleapis.com/token",
        )
        return build("drive", "v3", credentials=creds)

    def _list_files(self, service: Any, folder_name: str, query: str, max_results: int) -> str:
        q_parts = ["trashed = false"]
        if folder_name:
            # Find folder ID first
            folder_result = service.files().list(
                q=f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                fields="files(id)",
                pageSize=1,
            ).execute()
            folders = folder_result.get("files", [])
            if folders:
                q_parts.append(f"'{folders[0]['id']}' in parents")
        if query:
            q_parts.append(f"name contains '{query}'")

        result = service.files().list(
            q=" and ".join(q_parts),
            pageSize=max_results,
            fields="files(id, name, mimeType, size, modifiedTime, webViewLink)",
            orderBy="modifiedTime desc",
        ).execute()

        # If query returned nothing, fall back to listing all files so the agent
        # can find the closest match (handles typos, underscores vs spaces, etc.)
        if not result.get("files") and query:
            fallback_q = [p for p in q_parts if f"name contains" not in p]
            result = service.files().list(
                q=" and ".join(fallback_q) if fallback_q else "trashed = false",
                pageSize=max_results,
                fields="files(id, name, mimeType, size, modifiedTime, webViewLink)",
                orderBy="modifiedTime desc",
            ).execute()

        files = []
        for f in result.get("files", []):
            size_bytes = int(f.get("size", 0) or 0)
            files.append({
                "file_id":  f.get("id", ""),
                "name":     f.get("name", ""),
                "type":     _MIME_LABELS.get(f.get("mimeType", ""), f.get("mimeType", "")),
                "mime":     f.get("mimeType", ""),
                "size_kb":  round(size_bytes / 1024, 1),
                "modified": f.get("modifiedTime", ""),
                "web_url":  f.get("webViewLink", ""),
            })

        return json.dumps({"files": files, "total": len(files)}, ensure_ascii=False, default=str)

    def _download_file(self, service: Any, file_id: str, filename: str) -> str:
        # Get file metadata
        meta = service.files().get(
            fileId=file_id,
            fields="name, mimeType, size"
        ).execute()

        mime      = meta.get("mimeType", "")
        orig_name = filename or meta.get("name", "file")

        tmp_dir  = tempfile.gettempdir()
        file_path = os.path.join(tmp_dir, orig_name)

        # Google Workspace files need export; binary files use get_media
        if mime in _EXPORT_MAP:
            export_mime, ext = _EXPORT_MAP[mime]
            if not orig_name.endswith(ext):
                file_path += ext
            request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        else:
            request = service.files().get_media(fileId=file_id)

        import io
        fh = io.BytesIO()
        from googleapiclient.http import MediaIoBaseDownload
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        with open(file_path, "wb") as f:
            f.write(fh.getvalue())

        size_kb = round(len(fh.getvalue()) / 1024, 1)
        logger.info("ReadFromDriveTool: downloaded file_id=%s path=%s", file_id, file_path)
        return json.dumps({
            "status":    "downloaded",
            "file_path": file_path,
            "filename":  os.path.basename(file_path),
            "size_kb":   size_kb,
            "note":      "Pass file_path to read_attachment_content or summarize_document to read contents.",
        })

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            data = {}

        action      = data.get("action", "list")
        folder_name = data.get("folder_name", "")
        query       = data.get("query", "")
        max_results = int(data.get("max_results", 20))
        file_id     = data.get("file_id", "")
        filename    = data.get("filename", "")

        try:
            service = self._get_service()
            if action == "download":
                if not file_id:
                    return json.dumps({"error": "'file_id' is required for download. Use action='list' first."})
                return self._download_file(service, file_id, filename)
            else:
                return self._list_files(service, folder_name, query, max_results)
        except Exception as exc:
            logger.exception("ReadFromDriveTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "action":      {"type": "string", "enum": ["list", "download"],
                                "description": "'list' to browse files, 'download' to fetch a file"},
                "folder_name": {"type": "string", "description": "Filter by Drive folder name"},
                "query":       {"type": "string", "description": "Search term in filename"},
                "max_results": {"type": "integer", "description": "Max files to list (default 20)"},
                "file_id":     {"type": "string", "description": "Drive file ID (for download)"},
                "filename":    {"type": "string", "description": "Save filename override (optional)"},
            }},
        }}
