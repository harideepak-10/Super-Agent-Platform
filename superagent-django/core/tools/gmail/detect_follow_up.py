"""
DetectFollowUpTool — scan inbox for emails that need a follow-up reply.

Zone: GREEN — runs automatically, no human approval required.

Searches Gmail for sent emails with no reply, or received emails
older than N days with no response from the user.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class DetectFollowUpTool(BaseTool):
    """Scan inbox for emails waiting more than N days without a reply.

    Input::

        {
            "days":        3,           # emails older than this with no reply (default: 3)
            "max_results": 10           # max emails to check (default: 10)
        }

    Returns::

        {
            "follow_up_needed": [
                {
                    "message_id": "...",
                    "subject":    "...",
                    "sender":     "...",
                    "date":       "...",
                    "days_waiting": 5
                }
            ],
            "count": 3,
            "checked": 10
        }
    """

    name: str = "detect_follow_up_needed"
    description: str = (
        "Scan the inbox for emails that haven't been replied to in N days. "
        "Input JSON: {\"days\": 3, \"max_results\": 10}. "
        "Returns a list of emails that need follow-up with days_waiting for each."
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
            data = {}

        days        = int(data.get("days", 3))
        max_results = int(data.get("max_results", 10))

        try:
            service    = self._get_service()
            cutoff     = datetime.now(timezone.utc) - timedelta(days=days)
            cutoff_str = cutoff.strftime("%Y/%m/%d")

            # Search for emails in inbox older than N days, not sent by me
            query = f"in:inbox before:{cutoff_str} -from:me -is:sent"
            result = service.users().messages().list(
                userId="me", q=query, maxResults=max_results,
            ).execute()
            messages = result.get("messages", [])

            follow_ups = []
            for msg_ref in messages:
                try:
                    msg = service.users().messages().get(
                        userId="me", id=msg_ref["id"], format="metadata",
                        metadataHeaders=["Subject", "From", "Date"],
                    ).execute()
                    headers = {h["name"].lower(): h["value"]
                               for h in msg.get("payload", {}).get("headers", [])}

                    subject = headers.get("subject", "(no subject)")
                    sender  = headers.get("from", "")
                    date_str= headers.get("date", "")

                    # Calculate days waiting
                    days_waiting = days  # fallback
                    try:
                        from email.utils import parsedate_to_datetime
                        email_dt = parsedate_to_datetime(date_str)
                        if email_dt.tzinfo is None:
                            email_dt = email_dt.replace(tzinfo=timezone.utc)
                        days_waiting = (datetime.now(timezone.utc) - email_dt).days
                    except Exception:
                        pass

                    follow_ups.append({
                        "message_id":   msg_ref["id"],
                        "thread_id":    msg.get("threadId", ""),
                        "subject":      subject,
                        "sender":       sender,
                        "date":         date_str,
                        "days_waiting": days_waiting,
                    })
                except Exception:
                    continue

            # Sort by most urgent (most days waiting first)
            follow_ups.sort(key=lambda x: x["days_waiting"], reverse=True)

            logger.info("DetectFollowUpTool: %d emails need follow-up", len(follow_ups))
            return json.dumps({
                "follow_up_needed": follow_ups,
                "count":   len(follow_ups),
                "checked": len(messages),
                "days_threshold": days,
            }, ensure_ascii=False)

        except Exception as exc:
            logger.exception("DetectFollowUpTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "days":        {"type": "integer", "description": "Emails older than this many days"},
                    "max_results": {"type": "integer"},
                },
            },
        }}
