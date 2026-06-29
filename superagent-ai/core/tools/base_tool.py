"""
Base tool interface and zone definitions.

Every tool in the Super Agent platform inherits from BaseTool and
declares one of three safety zones:

    GREEN  — Runs automatically without any human approval.
    YELLOW — Pauses execution and waits for a human to approve or deny.
    RED    — Never executed by the agent; reserved for human action only.

The zone is enforced by BaseAgent, not by the tool itself — the tool
simply declares what zone it belongs to.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum


class ToolZone(str, Enum):
    """Safety zone classification for tools.

    GREEN  : Agent may run this tool without any human involvement.
    YELLOW : Agent must pause and request human approval before running.
    RED    : Agent is never allowed to run this tool — humans only.
    """

    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class BaseTool(ABC):
    """Abstract base class for all Super Agent tools.

    Subclasses must:
      - Set ``name`` to a unique snake_case identifier.
      - Set ``description`` to a clear, one-sentence explanation.
      - Set ``zone`` to the appropriate ``ToolZone`` value.
      - Implement ``run(input_str)`` with proper input validation.

    Attributes:
        name:        Unique snake_case identifier used by the LLM to
                     reference this tool.
        description: Human-readable description of what the tool does.
        zone:        Safety zone — controls whether the agent can run
                     the tool automatically or must ask for approval.
    """

    name: str = ""
    description: str = ""
    zone: ToolZone = ToolZone.GREEN

    @abstractmethod
    def run(self, input_str: str) -> str:
        """Execute the tool with the given input string.

        Args:
            input_str: The raw input provided by the agent (or human).
                       Subclasses are responsible for parsing and
                       validating this string before using it.

        Returns:
            A string result that will be fed back to the agent as a
            tool observation.

        Raises:
            ValueError: If ``input_str`` is invalid or cannot be parsed.
            NotImplementedError: If the subclass forgot to implement this.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement `run`."
        )

    def to_schema(self) -> dict:
        """Return a minimal tool schema dict for use in LLM calls.

        Returns:
            Dict with ``name``, ``description``, and ``zone``.
        """
        return {
            "name": self.name,
            "description": self.description,
            "zone": self.zone.value,
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} zone={self.zone.value}>"
