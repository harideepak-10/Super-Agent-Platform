"""
CheckDeadlinesTool — GREEN zone.
Scans a list of compliance items and returns overdue / upcoming items.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone


class CheckDeadlinesTool(BaseTool):
    """Check compliance deadlines and return overdue / upcoming items."""

    name = "check_deadlines"
    description = (
        "Check a list of compliance items for overdue or upcoming deadlines. "
        "Accepts a JSON list of items with 'id', 'name', 'due_date' (YYYY-MM-DD), "
        "and optional 'status'. Returns items grouped as overdue, due_today, and upcoming. "
        "Optional 'warning_days' (default 7) sets the upcoming window."
    )
    zone = ToolZone.GREEN

    def run(self, tool_input: str) -> str:
        try:
            data = json.loads(tool_input) if tool_input.strip().startswith("{") or tool_input.strip().startswith("[") else {}
            if isinstance(data, list):
                data = {"items": data}
        except (json.JSONDecodeError, AttributeError):
            return json.dumps({"error": "Invalid JSON input"})

        items: list[dict] = data.get("items", [])
        warning_days: int = int(data.get("warning_days", 7))
        reference_date_str: str | None = data.get("reference_date")  # for testing

        if not items:
            return json.dumps({"error": "No items provided", "overdue": [], "due_today": [], "upcoming": []})

        try:
            today = date.fromisoformat(reference_date_str) if reference_date_str else date.today()
        except ValueError:
            return json.dumps({"error": f"Invalid reference_date: {reference_date_str}"})

        overdue: list[dict] = []
        due_today: list[dict] = []
        upcoming: list[dict] = []
        ok: list[dict] = []

        for item in items:
            item_id = item.get("id", "unknown")
            name = item.get("name", "Unnamed")
            due_str = item.get("due_date", "")
            status = item.get("status", "pending")

            if status in ("completed", "done", "paid"):
                ok.append({"id": item_id, "name": name, "status": status})
                continue

            try:
                due = date.fromisoformat(due_str)
            except (ValueError, TypeError):
                overdue.append({
                    "id": item_id,
                    "name": name,
                    "due_date": due_str,
                    "status": status,
                    "days_overdue": None,
                    "note": "Invalid or missing due_date",
                })
                continue

            delta = (due - today).days

            entry = {
                "id": item_id,
                "name": name,
                "due_date": due_str,
                "status": status,
                "days_delta": delta,
            }

            if delta < 0:
                entry["days_overdue"] = abs(delta)
                overdue.append(entry)
            elif delta == 0:
                entry["days_until_due"] = 0
                due_today.append(entry)
            elif delta <= warning_days:
                entry["days_until_due"] = delta
                upcoming.append(entry)
            else:
                ok.append(entry)

        # Sort overdue by most overdue first
        overdue.sort(key=lambda x: x.get("days_overdue") or 0, reverse=True)
        # Sort upcoming by soonest first
        upcoming.sort(key=lambda x: x.get("days_until_due", 999))

        return json.dumps({
            "reference_date": today.isoformat(),
            "warning_days": warning_days,
            "overdue": overdue,
            "due_today": due_today,
            "upcoming": upcoming,
            "ok_count": len(ok),
            "total_checked": len(items),
            "action_required": len(overdue) + len(due_today) > 0,
        })
