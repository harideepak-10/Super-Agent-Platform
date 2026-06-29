"""
Current time tool — returns the current date, time, and day information.

Zone: GREEN — runs automatically, no human approval required.

Returns a structured string with:
  - Current date (YYYY-MM-DD)
  - Current time (HH:MM:SS)
  - Day of the week
  - Whether today is a weekday or weekend
"""

from __future__ import annotations

from datetime import datetime

from .base_tool import BaseTool, ToolZone


class CurrentTimeTool(BaseTool):
    """Return the current date, time, day of week, and weekday/weekend status.

    Input:
        Ignored — pass any string (or empty string).

    Returns:
        A multi-line string with current date/time information.

    Example output::

        Current Date  : 2024-11-05
        Current Time  : 14:32:07
        Day of Week   : Tuesday
        Day Type      : Weekday
    """

    name: str = "current_time"
    description: str = (
        "Returns the current date, time, day of the week, and whether today "
        "is a weekday or weekend. Input is ignored."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        """Return current date and time information.

        Args:
            input_str: Ignored — this tool takes no meaningful input.

        Returns:
            Formatted string containing current date, time, day of week,
            and weekday/weekend classification.
        """
        now = datetime.now()

        day_name = now.strftime("%A")  # e.g. "Tuesday"
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")
        day_type = "Weekend" if now.weekday() >= 5 else "Weekday"

        return (
            f"Current Date  : {date_str}\n"
            f"Current Time  : {time_str}\n"
            f"Day of Week   : {day_name}\n"
            f"Day Type      : {day_type}"
        )
