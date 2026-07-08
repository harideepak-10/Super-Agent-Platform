"""
MoveToFolderTool — move Gmail messages to a folder/category.

Zone: GREEN — runs automatically, no human approval required.
"""
from __future__ import annotations
import json
import logging
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

# Gmail built-in category labels
_FOLDER_MAP = {
    "inbox":       "INBOX",
    "sent":        "SENT",
    "spam":        "SPAM",
    "trash":       "TRASH",
    "starred":     "STARRED",
    "important":   "IMPORTANT",
    "personal":    "CATEGORY_PERSONAL",
    "social":      "CATEGORY_SOCIAL",
    "updates":     "CATEGORY_UPDATES",
    "forums":      "CATEGORY_FORUMS",
    "promotions":  "CATEGORY_PROMOTIONS",
}


class MoveToFolderTool(BaseTool):
    """Move Gmail messages to a specific folder or category.

    Input::

        {
            "message_ids": ["msg_id1", "msg_id2"],   OR "message_id": "..."
            "folder": "inbox"   # inbox | spam | trash | starred | important
                                # personal | social | updates | forums | promotions
                                # OR any custom label name
        }

    Returns::

        {"status": "moved", "folder": "inbox", "count": 2}
    """

    name: str = "move_to_folder"
    description: str = (
        "Move Gmail messages to a folder or category. "
        "Input JSON: {\"message_ids\": [...], \"folder\": \"inbox|spam|trash|starred|important|...\"}. "
        "Folder can be a Gmail category (inbox, spam, trash) or a custom label name."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, gmail_service: Any = None) -> None:
        self._injected_service = gmail_service
        self._service: Any = None

    def _get_service(self) -> Any:
        if self._injected_service is not None:
            return self._injected_service
        if self._service is None:
            from core.tools.gmail.auth import GmailAuth
            self._service = GmailAuth().build_service("default")
        return self._service

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        ids    = data.get("message_ids") or ([data["message_id"]] if data.get("message_id") else [])
        folder = (data.get("folder") or "").lower().strip()

        if not ids:
            return json.dumps({"error": "'message_ids' is required."})
        if not folder:
            return json.dumps({"error": "'folder' is required."})

        label_id = _FOLDER_MAP.get(folder, folder.upper())

        # Determine which labels to add/remove
        add_ids    = [label_id]
        remove_ids = []
        if folder == "trash":
            remove_ids = ["INBOX"]
        elif folder == "spam":
            remove_ids = ["INBOX"]
        elif folder == "inbox":
            remove_ids = ["SPAM", "TRASH"]

        try:
            service = self._get_service()
            for mid in ids:
                service.users().messages().modify(
                    userId="me", id=mid,
                    body={"addLabelIds": add_ids, "removeLabelIds": remove_ids},
                ).execute()
            logger.info("MoveToFolderTool: moved %d message(s) to %s", len(ids), folder)
            return json.dumps({"status": "moved", "folder": folder, "count": len(ids)})
        except Exception as exc:
            logger.exception("MoveToFolderTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "message_ids": {"type": "array", "items": {"type": "string"}},
                    "message_id":  {"type": "string"},
                    "folder":      {"type": "string",
                                   "description": "inbox|spam|trash|starred|important or custom label"},
                },
                "required": ["folder"],
            },
        }}
