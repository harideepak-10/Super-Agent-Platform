"""
LabelEmailTool — apply or remove Gmail labels on messages.

Zone: GREEN — runs automatically, no human approval required.
"""
from __future__ import annotations
import json
import logging
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class LabelEmailTool(BaseTool):
    """Apply or remove Gmail labels on one or more messages.

    Creates the label if it doesn't exist yet.

    Input::

        {
            "message_ids": ["msg_id1"],   OR "message_id": "msg_id1"
            "add_labels":    ["KRYPSOS/Done", "Important"],   (optional)
            "remove_labels": ["UNREAD"]                        (optional)
        }

    Returns::

        {"status": "labelled", "added": [...], "removed": [...], "count": 1}
    """

    name: str = "label_email"
    description: str = (
        "Apply or remove Gmail labels on messages. Creates labels if they don't exist. "
        "Input JSON: {\"message_ids\": [...], \"add_labels\": [...], \"remove_labels\": [...]}. "
        "Use labels like 'KRYPSOS/Done', 'KRYPSOS/Follow-up', 'KRYPSOS/Urgent' to organise emails."
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

    def _get_or_create_label(self, service, name: str) -> str:
        """Return label ID, creating the label if it doesn't exist."""
        # Check built-in labels first
        builtin = {"INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "UNREAD", "STARRED",
                   "IMPORTANT", "CATEGORY_PERSONAL", "CATEGORY_SOCIAL", "CATEGORY_UPDATES",
                   "CATEGORY_FORUMS", "CATEGORY_PROMOTIONS"}
        if name.upper() in builtin:
            return name.upper()

        result = service.users().labels().list(userId="me").execute()
        for label in result.get("labels", []):
            if label["name"].lower() == name.lower():
                return label["id"]

        # Create new label
        new_label = service.users().labels().create(
            userId="me",
            body={"name": name, "labelListVisibility": "labelShow",
                  "messageListVisibility": "show"},
        ).execute()
        return new_label["id"]

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        ids         = data.get("message_ids") or ([data["message_id"]] if data.get("message_id") else [])
        add_names   = data.get("add_labels", [])
        remove_names= data.get("remove_labels", [])

        if not ids:
            return json.dumps({"error": "'message_ids' is required."})
        if not add_names and not remove_names:
            return json.dumps({"error": "Provide 'add_labels' or 'remove_labels'."})

        try:
            service = self._get_service()
            add_ids    = [self._get_or_create_label(service, n) for n in add_names]
            remove_ids = [self._get_or_create_label(service, n) for n in remove_names]

            for mid in ids:
                service.users().messages().modify(
                    userId="me", id=mid,
                    body={"addLabelIds": add_ids, "removeLabelIds": remove_ids},
                ).execute()

            logger.info("LabelEmailTool: labelled %d message(s)", len(ids))
            return json.dumps({
                "status":  "labelled",
                "added":   add_names,
                "removed": remove_names,
                "count":   len(ids),
            })
        except Exception as exc:
            logger.exception("LabelEmailTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "message_ids":   {"type": "array", "items": {"type": "string"}},
                "message_id":    {"type": "string"},
                "add_labels":    {"type": "array", "items": {"type": "string"}},
                "remove_labels": {"type": "array", "items": {"type": "string"}},
            }},
        }}
