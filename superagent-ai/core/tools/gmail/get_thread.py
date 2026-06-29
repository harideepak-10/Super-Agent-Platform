"""
GetThreadTool — retrieve a full Gmail thread (GREEN zone).

Fetches all messages in a thread so the agent can understand
the full conversation context before drafting a reply.
"""

from __future__ import annotations
import base64
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone


def _decode_body(payload: dict) -> str:
    """Recursively extract text from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    if mime_type == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            import re
            raw = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            return re.sub(r"<[^>]+>", "", raw).strip()
    for part in payload.get("parts", []):
        text = _decode_body(part)
        if text:
            return text
    return ""


class GetThreadTool(BaseTool):
    name = "get_thread"
    description = (
        "Retrieve all messages in a Gmail thread to read the full conversation. "
        "Input: { thread_id: str }. "
        "Returns ordered list of messages with sender, date, and body."
    )
    zone = ToolZone.GREEN

    def __init__(self, gmail_service=None):
        self._service = gmail_service

    def _get_service(self):
        if self._service:
            return self._service
        from core.tools.gmail.auth import GmailAuth
        return GmailAuth().get_service()

    def run(self, tool_input: "str | dict[str, Any]") -> Any:
        if isinstance(tool_input, str):
            try:
                import json
                tool_input = json.loads(tool_input)
            except (json.JSONDecodeError, TypeError):
                tool_input = {}
        thread_id = tool_input.get("thread_id", "").strip()
        if not thread_id:
            return {"error": "thread_id is required"}

        service = self._get_service()

        thread = (
            service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )

        messages = []
        for msg in thread.get("messages", []):
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            body = _decode_body(msg.get("payload", {}))
            messages.append({
                "id": msg["id"],
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "date": headers.get("Date", ""),
                "subject": headers.get("Subject", ""),
                "body": body[:2000],  # cap at 2000 chars per message
                "snippet": msg.get("snippet", ""),
            })

        return {
            "thread_id": thread_id,
            "message_count": len(messages),
            "messages": messages,
        }
