"""
Echo tool — returns exactly what was sent to it.

Zone: GREEN — runs automatically, no human approval required.

Used exclusively for testing the tool invocation pipeline.
Passing through input unchanged makes it trivial to verify that the
agent correctly called a tool and received the result.
"""

from __future__ import annotations

from .base_tool import BaseTool, ToolZone


class EchoTool(BaseTool):
    """Return the input string unchanged.

    This tool exists purely for testing.  It proves that:
      - The agent can call a tool.
      - The tool result is correctly appended to the message history.
      - The agent can read the result and continue.

    Input:
        Any non-empty string.

    Returns:
        The exact same string that was passed in, prefixed with
        ``"Echo: "`` so test assertions are unambiguous.
    """

    name: str = "echo"
    description: str = (
        "Returns exactly what was sent to it. "
        "Use only for testing the tool system. "
        "Input: any string. Output: 'Echo: <input>'."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        """Echo the input back with a prefix.

        Args:
            input_str: Any string to echo back.

        Returns:
            ``"Echo: <input_str>"``

        Raises:
            ValueError: If input_str is None or empty.
        """
        if input_str is None:
            raise ValueError("EchoTool received None as input.")
        return f"Echo: {input_str}"
