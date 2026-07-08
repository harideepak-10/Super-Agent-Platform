"""
DownloadAttachmentTool — download an email attachment from Gmail.

Zone: GREEN — runs automatically, no human approval required.

Uses the attachment_id and message_id from read_emails response to
fetch the file bytes from Gmail API and save to /tmp/krypsos_docs/.
Returns the file_path so it can be read, summarized, or passed to
upload_to_drive.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_OUTPUT_DIR = os.path.join(tempfile.gettempdir(), "krypsos_docs")


class DownloadAttachmentTool(BaseTool):
    """Download an email attachment from Gmail and save it locally.

    Uses the attachment_id and message_id from the read_emails response.

    Input format (JSON string)::

        {
            "message_id":    "<gmail_message_id>",
            "attachment_id": "<gmail_attachment_id>",
            "filename":      "invoice.pdf"          (optional — uses Gmail filename if omitted)
        }

    Returns::

        {
            "status":    "downloaded",
            "filename":  "invoice.pdf",
            "file_path": "/tmp/krypsos_docs/invoice.pdf",
            "mime_type": "application/pdf",
            "size_kb":   48.2
        }

    Typical flow::

        read_emails → email has attachments[{filename, attachment_id, message_id}]
        → download_attachment (pass message_id + attachment_id)
        → file_path returned
        → (optional) upload_to_drive with file_path
    """

    name: str = "download_attachment"
    description: str = (
        "Download an email attachment from Gmail and save it to a local file. "
        "Input JSON: {\"message_id\": \"...\", \"attachment_id\": \"...\", \"filename\": \"...(optional)\"}. "
        "Get message_id and attachment_id from the 'attachments' list in read_emails response. "
        "Returns file_path of the saved file. Pass file_path to upload_to_drive if needed."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, gmail_service: Any = None) -> None:
        self._injected_service = gmail_service
        self._service: Any = None

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input. Expected JSON."})

        message_id    = data.get("message_id", "")
        attachment_id = data.get("attachment_id", "")
        filename      = data.get("filename", "attachment")

        if not message_id or not attachment_id:
            return json.dumps({
                "error": "Both 'message_id' and 'attachment_id' are required. "
                         "Get them from the 'attachments' list in read_emails response."
            })

        try:
            service = self._get_service()
            return self._download(service, message_id, attachment_id, filename)
        except ImportError:
            return json.dumps({"error": "Gmail service not available. Connect Gmail in Integrations."})
        except Exception as exc:
            logger.exception("DownloadAttachmentTool failed")
            return json.dumps({"error": f"Download failed: {exc}"})

    def _get_service(self) -> Any:
        if self._injected_service is not None:
            return self._injected_service
        if self._service is None:
            from core.tools.gmail.auth import GmailAuth
            self._service = GmailAuth().build_service("default")
        return self._service

    def _download(self, service: Any, message_id: str, attachment_id: str, filename: str) -> str:
        # Fetch attachment data from Gmail API
        attachment = (
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )

        raw_data = attachment.get("data", "")
        if not raw_data:
            return json.dumps({"error": "Attachment data is empty."})

        # Decode base64url to bytes
        file_bytes = base64.urlsafe_b64decode(raw_data + "==")

        # Save to disk
        os.makedirs(_OUTPUT_DIR, exist_ok=True)
        file_path = os.path.join(_OUTPUT_DIR, filename)

        # Avoid overwriting existing files — append suffix if needed
        if os.path.exists(file_path):
            name, ext = os.path.splitext(filename)
            import uuid
            file_path = os.path.join(_OUTPUT_DIR, f"{name}_{uuid.uuid4().hex[:6]}{ext}")
            filename = os.path.basename(file_path)

        with open(file_path, "wb") as f:
            f.write(file_bytes)

        size_kb = round(len(file_bytes) / 1024, 1)
        logger.info("DownloadAttachmentTool: saved %s (%.1f KB)", filename, size_kb)

        return json.dumps({
            "status":    "downloaded",
            "filename":  filename,
            "file_path": file_path,
            "size_kb":   size_kb,
        })

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "message_id":    {"type": "string", "description": "Gmail message ID from read_emails"},
                    "attachment_id": {"type": "string", "description": "Attachment ID from read_emails attachments list"},
                    "filename":      {"type": "string", "description": "Filename to save as (use the filename from read_emails)"},
                },
                "required": ["message_id", "attachment_id"],
            },
        }}
