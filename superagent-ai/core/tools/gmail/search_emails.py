"""
SearchEmailsTool — search Gmail by query string (GREEN zone).

Wraps the Gmail API messages.list with a q= search parameter,
supporting the full Gmail search syntax:
  from:boss@company.com subject:invoice is:unread newer_than:7d
"""

from __future__ import annotations
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone


class SearchEmailsTool(BaseTool):
    name = "search_emails"
    description = (
        "Search Gmail messages using Gmail search syntax. "
        "Input: { query: str, max_results: int = 10 }. "
        "Examples: 'from:boss@co.com is:unread', 'subject:invoice newer_than:7d'. "
        "Returns a list of matching emails with id, subject, sender, date, snippet."
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
        query = tool_input.get("query", "")
        max_results = int(tool_input.get("max_results", 10))

        if not query:
            return {"error": "query is required"}

        service = self._get_service()

        result = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )

        messages = result.get("messages", [])
        if not messages:
            return {"emails": [], "total": 0, "query": query}

        emails = []
        for msg_ref in messages:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_ref["id"], format="metadata",
                     metadataHeaders=["Subject", "From", "Date"])
                .execute()
            )
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            emails.append({
                "id": msg["id"],
                "thread_id": msg.get("threadId", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "from": headers.get("From", ""),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
                "labels": msg.get("labelIds", []),
            })

        return {"emails": emails, "total": len(emails), "query": query}
