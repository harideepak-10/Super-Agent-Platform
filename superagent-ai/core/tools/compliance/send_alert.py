"""
SendAlertTool — YELLOW zone.
Sends a compliance alert via Telegram.
Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.
All sends require human approval (YELLOW zone).
"""

from __future__ import annotations

import json
import os
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone


_ALERT_LEVELS = ("info", "warning", "critical")

_LEVEL_EMOJI = {
    "info": "ℹ️",
    "warning": "⚠️",
    "critical": "🚨",
}


class TelegramService:
    """Thin wrapper around the Telegram Bot API. Swap out in tests."""

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None):
        self._token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    def send_message(self, text: str) -> dict[str, Any]:
        """POST to Telegram sendMessage endpoint. Returns response dict."""
        if not self._token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set")
        if not self._chat_id:
            raise ValueError("TELEGRAM_CHAT_ID not set")

        import urllib.request, urllib.parse  # lazy import

        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = json.dumps({
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))


class SendAlertTool(BaseTool):
    """Send a compliance alert via Telegram (YELLOW — requires approval)."""

    name = "send_alert"
    description = (
        "Send a compliance alert via Telegram. "
        "Accepts 'message' (alert body), 'level' (info/warning/critical, default warning), "
        "and optional 'subject' for the alert heading. "
        "Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars. "
        "This tool is YELLOW zone — always requires human approval before sending."
    )
    zone = ToolZone.YELLOW

    def __init__(self, telegram_service: TelegramService | None = None):
        self._telegram = telegram_service or TelegramService()

    def run(self, tool_input: str) -> str:
        try:
            data = json.loads(tool_input) if tool_input.strip().startswith("{") else {}
        except (json.JSONDecodeError, AttributeError):
            return json.dumps({"error": "Invalid JSON input"})

        message: str = data.get("message", "").strip()
        level: str = data.get("level", "warning").lower()
        subject: str = data.get("subject", "Compliance Alert").strip()

        if not message:
            return json.dumps({"error": "No message provided"})

        if level not in _ALERT_LEVELS:
            return json.dumps({
                "error": f"Invalid level '{level}'. Must be one of: {', '.join(_ALERT_LEVELS)}"
            })

        emoji = _LEVEL_EMOJI[level]
        formatted_text = (
            f"{emoji} *{subject}*\n"
            f"Level: `{level.upper()}`\n\n"
            f"{message}"
        )

        try:
            response = self._telegram.send_message(formatted_text)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:
            return json.dumps({"error": f"Telegram error: {exc}"})

        return json.dumps({
            "status": "sent",
            "level": level,
            "subject": subject,
            "message": message,
            "telegram_message_id": response.get("result", {}).get("message_id"),
        })
