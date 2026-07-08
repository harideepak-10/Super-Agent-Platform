"""
UploadToDriveTool — upload a local file to Google Drive.

Zone: YELLOW — ALWAYS requires human approval before execution.

The user must have connected their Google Drive integration first
(GET /api/v1/integrations/drive/auth-url/ → authorize → callback saves tokens).

After upload the Drive file link is returned so the task runner can
add it to task.deliverables[].
"""

from __future__ import annotations

import json
import logging
import os

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class UploadToDriveTool(BaseTool):
    """Upload a local file to Google Drive after human approval.

    Zone: YELLOW — BaseAgent raises ApprovalRequired before this tool runs.

    Input format (JSON string)::

        {
            "file_path":   "/tmp/krypsos_docs/Q2_Report_20260708.pdf",
            "filename":    "Q2 Report 2026.pdf",    (optional — uses file_path basename)
            "folder_name": "KRYPSOS Reports",        (optional — uploads to root if omitted)
            "description": "Q2 sales report"         (optional)
        }

    Returns::

        {
            "status":    "uploaded",
            "filename":  "Q2 Report 2026.pdf",
            "drive_url": "https://drive.google.com/file/d/<id>/view",
            "file_id":   "<google_drive_file_id>",
            "folder":    "KRYPSOS Reports"
        }
    """

    name: str = "upload_to_drive"
    description: str = (
        "Upload a file to Google Drive. ALWAYS requires human approval (YELLOW zone). "
        "Input JSON: {\"file_path\": \"...\", \"filename\": \"...(optional)\", "
        "\"folder_name\": \"...(optional)\", \"description\": \"...(optional)\"}. "
        "Returns drive_url which is saved to task deliverables. "
        "Requires Google Drive integration to be connected first."
    )
    zone: ToolZone = ToolZone.YELLOW

    def __init__(self, workspace_id: str | None = None) -> None:
        self._workspace_id = workspace_id

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input. Expected JSON."})

        file_path   = data.get("file_path", "")
        filename    = data.get("filename") or os.path.basename(file_path)
        folder_name = data.get("folder_name", "")
        description = data.get("description", "")

        if not file_path:
            return json.dumps({"error": "'file_path' is required."})

        if not os.path.exists(file_path):
            return json.dumps({"error": f"File not found: {file_path}"})

        drive_service = self._get_drive_service()
        if not drive_service:
            return json.dumps({
                "error": "Google Drive not connected. Go to Integrations and connect Google Drive first.",
                "setup_url": "/api/v1/integrations/drive/auth-url/",
            })

        try:
            return self._upload(drive_service, file_path, filename, folder_name, description)
        except Exception as exc:
            logger.exception("UploadToDriveTool failed")
            return json.dumps({"error": f"Drive upload failed: {exc}"})

    def _get_drive_service(self):
        """Build a Google Drive service from the workspace Drive integration."""
        if not self._workspace_id:
            logger.warning("UploadToDriveTool: no workspace_id")
            return None
        try:
            from apps.integrations.models import Integration
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            integration = Integration.objects.filter(
                workspace_id=self._workspace_id,
                provider=Integration.Provider.GOOGLE_DRIVE,
                status=Integration.Status.ACTIVE,
            ).first()

            if not integration or not integration.access_token:
                return None

            creds = Credentials(
                token=integration.access_token,
                refresh_token=integration.refresh_token,
                client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
                client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
                token_uri="https://oauth2.googleapis.com/token",
            )
            return build("drive", "v3", credentials=creds)
        except Exception as exc:
            logger.warning("UploadToDriveTool._get_drive_service error: %s", exc)
            return None

    def _upload(self, service, file_path: str, filename: str, folder_name: str, description: str) -> str:
        from googleapiclient.http import MediaFileUpload

        # Detect MIME type
        ext = os.path.splitext(filename)[1].lower()
        mime_map = {
            ".pdf":  "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".csv":  "text/csv",
            ".txt":  "text/plain",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        mime_type = mime_map.get(ext, "application/octet-stream")

        # Resolve or create folder
        folder_id = None
        if folder_name:
            folder_id = self._get_or_create_folder(service, folder_name)

        file_metadata = {"name": filename, "description": description}
        if folder_id:
            file_metadata["parents"] = [folder_id]

        media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
        uploaded = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, webViewLink",
        ).execute()

        # Make it readable by anyone with the link
        service.permissions().create(
            fileId=uploaded["id"],
            body={"type": "anyone", "role": "reader"},
        ).execute()

        drive_url = uploaded.get("webViewLink", f"https://drive.google.com/file/d/{uploaded['id']}/view")

        logger.info("UploadToDriveTool: uploaded %s → %s", filename, drive_url)

        # Clean up local temp file after successful upload
        try:
            os.remove(file_path)
        except OSError:
            pass

        return json.dumps({
            "status":    "uploaded",
            "filename":  uploaded.get("name", filename),
            "drive_url": drive_url,
            "file_id":   uploaded["id"],
            "folder":    folder_name or "My Drive (root)",
        })

    @staticmethod
    def _get_or_create_folder(service, folder_name: str) -> str | None:
        """Return the Drive folder ID, creating the folder if it doesn't exist."""
        try:
            query = (
                f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' "
                f"and trashed=false"
            )
            results = service.files().list(q=query, fields="files(id, name)").execute()
            files = results.get("files", [])
            if files:
                return files[0]["id"]

            # Create folder
            folder = service.files().create(
                body={
                    "name": folder_name,
                    "mimeType": "application/vnd.google-apps.folder",
                },
                fields="id",
            ).execute()
            return folder["id"]
        except Exception as exc:
            logger.warning("Could not get/create folder '%s': %s", folder_name, exc)
            return None

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "file_path":   {"type": "string", "description": "Local path from create_pdf/create_docx/export_csv"},
                    "filename":    {"type": "string", "description": "Display name in Drive"},
                    "folder_name": {"type": "string", "description": "Drive folder name (created if missing)"},
                    "description": {"type": "string"},
                },
                "required": ["file_path"],
            },
        }}
