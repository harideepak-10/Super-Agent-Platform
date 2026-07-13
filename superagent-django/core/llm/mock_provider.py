"""
Mock LLM provider for testing.

MockLLMProvider replays a fixed list of pre-written responses in order.
It never makes a real network call and is the ONLY provider used in
the test suite.  Using it in production is a programming error.
"""

from __future__ import annotations

from typing import Any

from .base import LLMProvider


class MockLLMProvider(LLMProvider):
    """Deterministic LLM provider driven by a pre-written response list.

    Responses are consumed one by one each time ``send`` is called.
    When the list is exhausted, further calls raise ``StopIteration``
    so test authors notice they did not supply enough canned responses.

    Attributes:
        _responses:    Ordered list of response dicts to replay.
        call_count:    Number of times ``send`` has been called so far.
        total_tokens:  Cumulative fake token count across all calls.
    """

    FAKE_TOKENS_PER_CALL: int = 150  # deterministic token count per call

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        """Initialise the mock with a list of canned responses.

        Each response dict must match the shape documented in
        ``LLMProvider.send``:
            {
                "content":    str,
                "tool_call":  {"name": str, "input": str} | None,
                "tokens_used": int,   # optional — defaults to FAKE_TOKENS_PER_CALL
                "cost_eur":   float,  # optional — defaults to 0.0
            }

        Args:
            responses: Ordered list of canned responses to replay.
        """
        self._responses: list[dict[str, Any]] = list(responses)
        self._index: int = 0
        self.call_count: int = 0
        self.total_tokens: int = 0

    # ------------------------------------------------------------------
    # LLMProvider interface
    # ------------------------------------------------------------------

    def send(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        force_tool: bool = False,
    ) -> dict[str, Any]:
        """Return the next pre-written response from the list.

        Args:
            messages: Ignored — mock does not process messages.
            tools:    Ignored — mock does not inspect tool schemas.

        Returns:
            The next response dict from the canned list, normalised to
            always contain ``tokens_used`` and ``cost_eur``.

        Raises:
            StopIteration: If the pre-written response list is exhausted.
        """
        if self._index >= len(self._responses):
            raise StopIteration(
                f"MockLLMProvider exhausted all {len(self._responses)} "
                "pre-written responses.  Add more entries to the list."
            )

        response = dict(self._responses[self._index])  # shallow copy
        self._index += 1
        self.call_count += 1

        # Ensure required keys are present with sensible defaults
        response.setdefault("tokens_used", self.FAKE_TOKENS_PER_CALL)
        response.setdefault("cost_eur", 0.0)
        response.setdefault("content", "")
        response.setdefault("tool_call", None)

        self.total_tokens += response["tokens_used"]
        return response

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset the mock to its initial state (useful between test runs)."""
        self._index = 0
        self.call_count = 0
        self.total_tokens = 0

    def remaining_responses(self) -> int:
        """Return the number of canned responses not yet consumed."""
        return len(self._responses) - self._index
