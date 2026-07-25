"""
Anthropic (Claude) LLM provider.

Wraps the official ``anthropic`` Python SDK to call Claude models.
Reads the API key from the ANTHROPIC_API_KEY environment variable.
Retries up to 3 times on transient failures and tracks token usage
and estimated cost per call.

Never use this in tests — use MockLLMProvider instead.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .base import LLMProvider


# ---------------------------------------------------------------------------
# Cost constants — Anthropic introductory pricing, valid through 2026-08-31.
# After that date it becomes $3 / $15 per million tokens — update then.
# See https://platform.claude.com/docs/en/about-claude/pricing
# ---------------------------------------------------------------------------
_COST_PER_1K_INPUT_TOKENS: float = 0.002    # USD ($2 / 1M input tokens, intro rate)
_COST_PER_1K_OUTPUT_TOKENS: float = 0.010   # USD ($10 / 1M output tokens, intro rate)
_USD_TO_EUR: float = 0.92
_MAX_RETRIES: int = 2
_RETRY_DELAY_SECONDS: float = 2.0
_REQUEST_TIMEOUT_SECONDS: float = 45.0
_MODEL: str = "claude-haiku-4-5-20251001"
_MAX_TOKENS: int = 4096
_MAX_RATE_LIMIT_RETRIES: int = 1
_RATE_LIMIT_WAIT_SECONDS: float = 15.0

_RATE_LIMIT_MESSAGE = (
    "⚠️ The AI is temporarily busy due to high usage (Anthropic rate limit reached). "
    "Please try again later."
)


class AnthropicRateLimitError(RuntimeError):
    """Raised when Anthropic returns a 429 / overloaded error."""
    pass


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "429" in msg
        or "rate_limit_error" in msg
        or "rate limit" in msg
        or "overloaded" in msg
    )


class AnthropicProvider(LLMProvider):
    """LLM provider backed by the Anthropic API.

    Attributes:
        total_tokens: Cumulative tokens used across all calls this session.
        total_cost:   Cumulative cost (EUR) across all calls this session.
    """

    def __init__(self, model: str = _MODEL, max_tokens: int = _MAX_TOKENS) -> None:
        """Initialise the Anthropic client.

        Args:
            model: Claude model name to use (default: claude-haiku-4-5-20251001).
            max_tokens: Max tokens to generate per call.

        Raises:
            EnvironmentError: If ANTHROPIC_API_KEY is not set.
        """
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY environment variable is not set. "
                "Add it to your .env file or export it before running."
            )

        try:
            from anthropic import Anthropic  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is not installed.  "
                "Run: pip install anthropic"
            ) from exc

        self._client = Anthropic(api_key=api_key, timeout=_REQUEST_TIMEOUT_SECONDS, max_retries=0)
        self._model = model
        self._max_tokens = max_tokens
        self.total_tokens: int = 0
        self.total_cost: float = 0.0

    # ------------------------------------------------------------------
    # LLMProvider interface
    # ------------------------------------------------------------------

    def send(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        force_tool: bool = False,
    ) -> dict[str, Any]:
        """Send messages to Claude and return a normalised response dict.

        Retries up to ``_MAX_RETRIES`` times on API errors before raising.
        """
        import logging as _logging
        _log = _logging.getLogger(__name__)
        last_error: Exception | None = None
        rate_limit_waits: int = 0
        normal_attempts: int = 0

        while True:
            try:
                return self._call_api(messages, tools, force_tool=force_tool)
            except Exception as exc:  # noqa: BLE001
                if _is_rate_limit_error(exc):
                    rate_limit_waits += 1
                    if rate_limit_waits <= _MAX_RATE_LIMIT_RETRIES:
                        _log.warning(
                            "Anthropic rate limit hit — waiting %.0fs before retry %d/%d",
                            _RATE_LIMIT_WAIT_SECONDS, rate_limit_waits, _MAX_RATE_LIMIT_RETRIES,
                        )
                        time.sleep(_RATE_LIMIT_WAIT_SECONDS)
                        continue
                    raise AnthropicRateLimitError(_RATE_LIMIT_MESSAGE) from exc
                normal_attempts += 1
                last_error = exc
                if normal_attempts < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY_SECONDS * normal_attempts)
                else:
                    raise RuntimeError(
                        f"Anthropic API call failed after {_MAX_RETRIES} attempts. "
                        f"Last error: {last_error}"
                    ) from last_error

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _translate_messages(
        self, messages: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert BaseAgent internal message format to Anthropic format.

        BaseAgent stores:
          {"role": "system", "content": "..."}
          {"role": "user", "content": "..."}
          {"role": "assistant", "content": "...", "tool_call": {"name": ..., "input": ...}}
          {"role": "tool", "name": ..., "content": ...}

        Anthropic expects:
          - a separate top-level ``system`` string (not inside the messages list)
          - {"role": "user" | "assistant", "content": [ {block}, ... ]}
          - tool calls as {"type": "tool_use", "id": ..., "name": ..., "input": {...}}
          - tool results as {"type": "tool_result", "tool_use_id": ..., "content": ...}
            sent back inside a "user" message
        """
        system_parts: list[str] = []
        translated: list[dict[str, Any]] = []
        last_call_id: str = "toolu_0"
        call_counter: int = 0

        for msg in messages:
            role = msg.get("role", "")

            if role == "system":
                if msg.get("content"):
                    system_parts.append(str(msg["content"]))
                continue

            if role == "assistant" and msg.get("tool_call"):
                tc = msg["tool_call"]
                name = tc.get("name", "tool")
                arguments = tc.get("input", "{}")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments) if arguments else {}
                    except json.JSONDecodeError:
                        arguments = {"_raw": arguments}
                call_counter += 1
                call_id = "toolu_{}_{}".format(name, call_counter)
                last_call_id = call_id

                blocks: list[dict[str, Any]] = []
                if msg.get("content"):
                    blocks.append({"type": "text", "text": msg["content"]})
                blocks.append({
                    "type": "tool_use",
                    "id": call_id,
                    "name": name,
                    "input": arguments,
                })
                translated.append({"role": "assistant", "content": blocks})

            elif role == "tool":
                translated.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": last_call_id,
                        "content": str(msg.get("content", "")),
                    }],
                })

            else:
                # user / plain assistant — pass through as a text block
                translated.append({
                    "role": role,
                    "content": [{"type": "text", "text": msg.get("content", "")}],
                })

        # Anthropic requires alternating user/assistant turns — merge any
        # consecutive same-role messages (e.g. several tool results in a row).
        merged: list[dict[str, Any]] = []
        for m in translated:
            if merged and merged[-1]["role"] == m["role"]:
                merged[-1]["content"].extend(m["content"])
            else:
                merged.append(m)

        return "\n\n".join(system_parts), merged

    @staticmethod
    def _translate_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        """Convert OpenAI/Groq-style function-calling tool schemas to Anthropic's format.

        OpenAI style:
          {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
        Anthropic style:
          {"name": ..., "description": ..., "input_schema": {...}}
        """
        if not tools:
            return None
        translated = []
        for t in tools:
            if "function" in t:
                fn = t["function"]
                translated.append({
                    "name": fn.get("name"),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                })
            else:
                translated.append(t)  # already Anthropic-shaped
        return translated

    def _call_api(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        force_tool: bool = False,
    ) -> dict[str, Any]:
        """Make a single API call and parse the response."""
        system_text, translated = self._translate_messages(messages)
        anthropic_tools = self._translate_tools(tools)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": translated,
        }
        if system_text:
            kwargs["system"] = system_text
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
            kwargs["tool_choice"] = {"type": "any"} if force_tool else {"type": "auto"}

        completion = self._client.messages.create(**kwargs)
        return self._parse_response(completion)

    def _parse_response(self, completion: Any) -> dict[str, Any]:
        """Convert an Anthropic Message object into our standard response dict."""
        content_parts: list[str] = []
        tool_call: dict[str, Any] | None = None

        for block in completion.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use" and tool_call is None:
                # Only the first tool call is surfaced — same contract as GroqProvider.
                tool_call = {
                    "name": block.name,
                    "input": json.dumps(block.input),  # raw JSON string, matches old contract
                }

        content = "".join(content_parts)

        usage = completion.usage
        input_tokens: int = getattr(usage, "input_tokens", 0)
        output_tokens: int = getattr(usage, "output_tokens", 0)
        tokens_used: int = input_tokens + output_tokens

        cost_eur: float = (
            (input_tokens / 1000) * _COST_PER_1K_INPUT_TOKENS
            + (output_tokens / 1000) * _COST_PER_1K_OUTPUT_TOKENS
        ) * _USD_TO_EUR

        self.total_tokens += tokens_used
        self.total_cost += cost_eur

        return {
            "content": content,
            "tool_call": tool_call,
            "tokens_used": tokens_used,
            "cost_eur": cost_eur,
        }

    def get_cost_summary(self) -> dict[str, Any]:
        """Return cumulative token and cost totals for this provider instance."""
        return {
            "total_tokens": self.total_tokens,
            "total_cost_eur": round(self.total_cost, 6),
        }