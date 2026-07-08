"""
ScheduleEmailTool — schedule an email to be sent at a future time.

Zone: YELLOW — requires human approval before scheduling.

Uses Celery's eta to schedule the actual send via send_email task.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Any
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class ScheduleEmailTool(BaseTool):
    """Schedule an email to be sent at a specific future time.

    Zone: YELLOW — BaseAgent raises ApprovalRequired before scheduling.

    Input::

        {
            "to":          "recipient@example.com",
            "subject":     "Meeting Tomorrow",
            "body":        "Hi, just a reminder...",
            "send_at":     "2026-07-09T11:00:00"   ← ISO 8601, local time assumed UTC
        }

    Returns::

        {
            "status":      "scheduled",
            "to":          "recipient@example.com",
            "subject":     "...",
            "send_at":     "2026-07-09T11:00:00+00:00",
            "celery_task_id": "..."
        }
    """

    name: str = "schedule_email"
    description: str = (
        "Schedule an email to be sent at a specific future time. REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"to\": \"...\", \"subject\": \"...\", \"body\": \"...\", \"send_at\": \"2026-07-09T11:00:00\"}. "
        "send_at must be an ISO 8601 datetime string."
    )
    zone: ToolZone = ToolZone.YELLOW

    def __init__(self, gmail_service: Any = None, workspace_id: str | None = None) -> None:
        self._gmail_service = gmail_service
        self._workspace_id  = workspace_id

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        to      = data.get("to", "")
        subject = data.get("subject", "")
        body    = data.get("body", "")
        send_at = data.get("send_at", "")

        if not all([to, subject, body, send_at]):
            return json.dumps({"error": "'to', 'subject', 'body', and 'send_at' are required."})

        # Parse send_at
        try:
            if send_at.endswith("Z"):
                send_at = send_at[:-1] + "+00:00"
            eta = datetime.fromisoformat(send_at)
            if eta.tzinfo is None:
                eta = eta.replace(tzinfo=timezone.utc)
        except ValueError:
            return json.dumps({"error": f"Invalid send_at format: {send_at!r}. Use ISO 8601."})

        now = datetime.now(timezone.utc)
        if eta <= now:
            return json.dumps({"error": "send_at must be in the future."})

        try:
            # Schedule via Celery eta
            from apps.tasks.tasks import send_scheduled_email
            result = send_scheduled_email.apply_async(
                kwargs={
                    "to":           to,
                    "subject":      subject,
                    "body":         body,
                    "workspace_id": self._workspace_id,
                },
                eta=eta,
            )
            logger.info("ScheduleEmailTool: scheduled to %s at %s (task=%s)", to, eta, result.id)
            return json.dumps({
                "status":         "scheduled",
                "to":             to,
                "subject":        subject,
                "send_at":        eta.isoformat(),
                "celery_task_id": result.id,
            })
        except Exception as exc:
            logger.exception("ScheduleEmailTool failed")
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "to":      {"type": "string"},
                    "subject": {"type": "string"},
                    "body":    {"type": "string"},
                    "send_at": {"type": "string", "description": "ISO 8601 datetime e.g. 2026-07-09T11:00:00"},
                },
                "required": ["to", "subject", "body", "send_at"],
            },
        }}
