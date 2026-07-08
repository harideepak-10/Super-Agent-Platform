"""
DeleteEmailTool — move Gmail messages to trash.

Zone: YELLOW — requires human approval before execution.
"""
from __future__ import annotations
import json
import logging
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class DeleteEmailTool(BaseTool):
    """Move Gmail messages to trash (requires approval).

    Zone: YELLOW — BaseAgent raises ApprovalRequired before this runs.

    Input::

        {"message_ids": ["msg_id1", "msg_id2"]}
        OR
        {"message_id": "msg_id1"}

    Returns::

        {"status": "trashed", "count": 2, "message_ids": [...]}
    """

    name: str = "delete_email"
    description: str = (
        "Move Gmail messages to trash. REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"message_ids\": [\"...\", \"...\"]} or {\"message_id\": \"...\"}. "
        "Messages are moved to trash, not permanently deleted."
    )
    zone: ToolZone = ToolZone.YELLOW

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

        ids = data.get("message_ids") or ([data["message_id"]] if data.get("message_id") else [])
        if not ids:
            return json.dumps({"error": "'message_ids' or 'message_id' is required."})

        try:
            service = self._get_service()
            for mid in ids:
                service.users().messages().trash(userId="me", id=mid).execute()
            logger.info("DeleteEmailTool: trashed %d message(s)", len(ids))
            return json.dumps({"status": "trashed", "count": len(ids), "message_ids": ids})
        except Exception as exc:
            logger.exception("DeleteEmailTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "message_ids": {"type": "array", "items": {"type": "string"}},
                "message_id":  {"type": "string"},
            }},
        }}
