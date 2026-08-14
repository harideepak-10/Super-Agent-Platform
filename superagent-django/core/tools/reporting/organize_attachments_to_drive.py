"""
OrganizeAttachmentsToDriveTool — create a dated Drive folder, organize it by
section, and upload a batch of already-downloaded attachments into it.

Zone: YELLOW — ALWAYS requires human approval before execution, same as
every other Drive write in this system (upload_to_drive). This is the
"commit" half of the find_daily_attachments → organize_attachments_to_drive
pair: find_daily_attachments (GREEN) discovers and downloads attachments
locally and classifies them; this tool does the actual external write, as
ONE approval covering the whole batch — not one approval per file, which
would be unusable for a routine EOD sweep.
"""
from __future__ import annotations

import json
import logging
import os

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_MIME_MAP = {
    ".pdf":  "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc":  "application/msword",
    ".csv":  "text/csv",
    ".txt":  "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".gif":  "image/gif",
    ".webp": "image/webp",
}


class OrganizeAttachmentsToDriveTool(BaseTool):
    """Create Root/Date/Section Drive folders and upload a batch of local files into them.

    Input::

        {
            "date": "2026-08-13",
            "root_folder_name": "Attachments",   (optional, default "Attachments")
            "attachments": [
                {"local_path": "/tmp/.../invoice.pdf", "filename": "invoice.pdf", "section": "Invoices"},
                ...
            ]
        }

    Pass the "attachments" list straight from find_daily_attachments's result.

    Returns::

        {
            "date": "2026-08-13",
            "root_folder_name": "Attachments",
            "uploaded_count": 4,
            "duplicates_skipped_count": 1,
            "failed_count": 0,
            "uploaded": [
                {"filename": "...", "section": "Invoices", "drive_url": "...", "folder_path": "Attachments/2026-08-13/Invoices"},
                ...
            ],
            "duplicates_skipped": [
                {"filename": "...", "section": "Invoices", "folder_path": "Attachments/2026-08-13/Invoices"},
                ...
            ],
            "failed": [ {"filename": "...", "error": "..."} , ... ]
        }
    """

    name: str = "organize_attachments_to_drive"
    description: str = (
        "Create a dated Google Drive folder, organized by section, and upload a batch of "
        "already-downloaded attachments into it (Root/Date/Section structure). Automatically "
        "skips any file that already exists (by exact name) in its destination folder — safe "
        "to call again on the same day without creating duplicates. ALWAYS requires "
        "human approval (YELLOW zone) — this is one approval for the whole batch, not one per "
        "file. Input JSON: {\"date\": \"2026-08-13\", \"root_folder_name\": \"Attachments\"(optional), "
        "\"attachments\": [the exact 'attachments' list returned by find_daily_attachments]}. "
        "Always call find_daily_attachments FIRST and pass its real attachments list here — "
        "never invent attachment entries. Returns the real Drive links for every file actually "
        "uploaded, which ones were skipped as duplicates, and honestly reports any that failed."
    )
    zone: ToolZone = ToolZone.YELLOW

    def __init__(self, workspace_id: str | None = None) -> None:
        self._workspace_id = workspace_id

    # ------------------------------------------------------------------
    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input. Expected JSON."})

        if not isinstance(data, dict):
            data = {}

        date_label = str(data.get("date") or "").strip()
        root_folder_name = str(data.get("root_folder_name") or "Attachments").strip()
        attachments = data.get("attachments") or []

        if not date_label:
            return json.dumps({"error": "'date' is required (e.g. '2026-08-13')."})
        if not isinstance(attachments, list) or not attachments:
            return json.dumps({"error": "'attachments' must be a non-empty list — call find_daily_attachments first."})

        drive_service = self._get_drive_service()
        if not drive_service:
            return json.dumps({
                "error": "Google Drive not connected. Go to Integrations and connect Google Drive first.",
                "setup_url": "/api/v1/integrations/drive/auth-url/",
            })

        root_id = self._get_or_create_folder(drive_service, root_folder_name, parent_id=None)
        if not root_id:
            return json.dumps({"error": f"Could not create/find root folder '{root_folder_name}' in Drive."})

        date_id = self._get_or_create_folder(drive_service, date_label, parent_id=root_id)
        if not date_id:
            return json.dumps({"error": f"Could not create/find date folder '{date_label}' in Drive."})

        section_folder_ids: dict[str, str] = {}
        uploaded = []
        duplicates_skipped = []
        failed = []

        for att in attachments:
            local_path = att.get("local_path", "")
            filename = att.get("filename") or (os.path.basename(local_path) if local_path else "")
            section = att.get("section") or "Other"

            if not local_path or not os.path.exists(local_path):
                failed.append({"filename": filename or "(unknown)", "error": f"Local file not found: {local_path}"})
                continue

            try:
                if section not in section_folder_ids:
                    section_id = self._get_or_create_folder(drive_service, section, parent_id=date_id)
                    if not section_id:
                        failed.append({"filename": filename, "error": f"Could not create/find section folder '{section}'."})
                        continue
                    section_folder_ids[section] = section_id
                section_id = section_folder_ids[section]

                if self._file_exists_in_folder(drive_service, filename, section_id):
                    duplicates_skipped.append({
                        "filename": filename,
                        "section": section,
                        "folder_path": f"{root_folder_name}/{date_label}/{section}",
                    })
                    try:
                        os.remove(local_path)
                    except OSError:
                        pass
                    continue

                drive_url, file_id = self._upload_one(drive_service, local_path, filename, section_id)
                uploaded.append({
                    "filename": filename,
                    "section": section,
                    "drive_url": drive_url,
                    "file_id": file_id,
                    "folder_path": f"{root_folder_name}/{date_label}/{section}",
                })
                try:
                    os.remove(local_path)
                except OSError:
                    pass
            except Exception as exc:
                logger.exception("OrganizeAttachmentsToDriveTool: upload failed for %s", filename)
                failed.append({"filename": filename, "error": str(exc)})

        return json.dumps({
            "date": date_label,
            "root_folder_name": root_folder_name,
            "uploaded_count": len(uploaded),
            "duplicates_skipped_count": len(duplicates_skipped),
            "failed_count": len(failed),
            "uploaded": uploaded,
            "duplicates_skipped": duplicates_skipped,
            "failed": failed,
        }, ensure_ascii=False)

    # ------------------------------------------------------------------
    def _get_drive_service(self):
        """Build a Google Drive service from the workspace Drive integration.

        Mirrors core/tools/document/upload_to_drive.py's implementation
        exactly — kept as a separate copy here rather than importing from
        that tool, so this file has no dependency on another tool's internals.
        """
        if not self._workspace_id:
            logger.warning("OrganizeAttachmentsToDriveTool: no workspace_id")
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
            logger.warning("OrganizeAttachmentsToDriveTool._get_drive_service error: %s", exc)
            return None

    @staticmethod
    def _get_or_create_folder(service, folder_name: str, parent_id: str | None):
        """Return the Drive folder ID under the given parent, creating it if needed.

        Unlike upload_to_drive.py's flat _get_or_create_folder (which searches
        by name globally with no parent constraint), this ALWAYS scopes the
        lookup to a specific parent — required for correct nesting, since
        e.g. two different date folders can each legitimately contain their
        own "Invoices" subfolder with the same name.
        """
        try:
            safe_name = folder_name.replace("'", "\\'")
            query = f"name='{safe_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            if parent_id:
                query += f" and '{parent_id}' in parents"
            else:
                query += " and 'root' in parents"

            results = service.files().list(q=query, fields="files(id, name)").execute()
            files = results.get("files", [])
            if files:
                return files[0]["id"]

            metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
            if parent_id:
                metadata["parents"] = [parent_id]
            folder = service.files().create(body=metadata, fields="id").execute()
            return folder["id"]
        except Exception as exc:
            logger.warning("Could not get/create folder '%s' under parent=%s: %s", folder_name, parent_id, exc)
            return None

    @staticmethod
    def _file_exists_in_folder(service, filename: str, parent_id: str) -> bool:
        """De-duplication check — True if a file with this exact name already
        exists directly inside the given Drive folder. Checked immediately
        before every upload, so re-running this tool (e.g. the user asks
        again, or retries after a partial failure) never creates duplicates.
        Mirrors _nightly_file_exists_in_folder in apps/tasks/tasks.py, used
        by the scheduled nightly sweep — kept as a separate copy so this
        tool has no dependency on that job's internals, same reasoning as
        why the nightly job doesn't import this file's upload logic either.
        """
        try:
            safe_name = filename.replace("'", "\\'")
            query = f"name='{safe_name}' and trashed=false and '{parent_id}' in parents"
            results = service.files().list(q=query, fields="files(id)").execute()
            return bool(results.get("files", []))
        except Exception as exc:
            logger.warning("Could not check for duplicate '%s' in folder=%s: %s", filename, parent_id, exc)
            # If the check itself fails, err on the side of NOT uploading a
            # possible duplicate — a missed upload is caught next time; a
            # duplicate file cannot be un-created.
            return True

    @staticmethod
    def _upload_one(service, file_path: str, filename: str, parent_id: str):
        from googleapiclient.http import MediaFileUpload

        ext = os.path.splitext(filename)[1].lower()
        mime_type = _MIME_MAP.get(ext, "application/octet-stream")

        media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
        uploaded = service.files().create(
            body={"name": filename, "parents": [parent_id]},
            media_body=media,
            fields="id, name, webViewLink",
        ).execute()

        service.permissions().create(
            fileId=uploaded["id"],
            body={"type": "anyone", "role": "reader"},
        ).execute()

        drive_url = uploaded.get("webViewLink", f"https://drive.google.com/file/d/{uploaded['id']}/view")
        return drive_url, uploaded["id"]

    # ------------------------------------------------------------------
    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date label matching find_daily_attachments's output, e.g. '2026-08-13'"},
                    "root_folder_name": {"type": "string", "description": "Top-level Drive folder name (default 'Attachments')"},
                    "attachments": {
                        "type": "array",
                        "description": "The exact 'attachments' list from find_daily_attachments's result",
                        "items": {
                            "type": "object",
                            "properties": {
                                "local_path": {"type": "string"},
                                "filename": {"type": "string"},
                                "section": {"type": "string"},
                            },
                        },
                    },
                },
                "required": ["date", "attachments"],
            },
        }}