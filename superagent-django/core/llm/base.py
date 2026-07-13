"""
Base LLM provider interface.

Every concrete LLM provider (Groq, OpenAI, mock, etc.) must extend
LLMProvider and implement the `send` method.  Nothing in this file
knows about any specific vendor — it is pure interface definition.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """Abstract base class for all LLM providers.

    Concrete subclasses must implement `send`, which accepts a list of
    chat messages and an optional list of tool schemas and returns the
    raw model response.
    """

    @abstractmethod
    def send(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        force_tool: bool = False,
    ) -> dict[str, Any]:
        """Send a conversation to the language model and return its response.

        Args:
            messages: List of chat messages in the standard
                      ``{"role": ..., "content": ...}`` format.
            tools:    Optional list of tool schemas in the format the
                      provider expects (e.g. OpenAI function-calling spec).

        Returns:
            A dictionary representing the model response.  Must contain
            at minimum:
                - ``"content"``   : str  — the text response (may be empty)
                - ``"tool_call"`` : dict | None — chosen tool, if any
                    - ``"name"``  : str — tool name
                    - ``"input"`` : str — raw input string for the tool
                - ``"tokens_used"`` : int — total tokens consumed
                - ``"cost_usd"``    : float — estimated cost in USD

        Raises:
            NotImplementedError: If a subclass forgets to implement this.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement `send`."
        )
