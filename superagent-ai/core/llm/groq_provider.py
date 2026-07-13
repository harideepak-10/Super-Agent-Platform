"""
Groq LLM provider.

Wraps the official ``groq`` Python SDK to call llama-3.3-70b-versatile.
Reads the API key from the GROQ_API_KEY environment variable.
Retries up to 3 times on transient failures and tracks token usage
and estimated cost per call.

Never use this in tests — use MockLLMProvider instead.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .base import LLMProvider


# ---------------------------------------------------------------------------
# Cost constants (as of mid-2024 Groq pricing for llama-3.3-70b-versatile)
# ---------------------------------------------------------------------------
_COST_PER_1K_INPUT_TOKENS: float = 0.00005   # USD (converted to EUR on output)
_COST_PER_1K_OUTPUT_TOKENS: float = 0.00008  # USD (converted to EUR on output)
_USD_TO_EUR: float = 0.92
_MAX_RETRIES: int = 3
_RETRY_DELAY_SECONDS: float = 2.0
_MODEL: str = "llama-3.3-70b-versatile"


class GroqProvider(LLMProvider):
    """LLM provider backed by the Groq API.

    Attributes:
        total_tokens: Cumulative tokens used across all calls this session.
        total_cost:   Cumulative cost (EUR) across all calls this session.
    """

    def __init__(self, model: str = _MODEL) -> None:
        """Initialise the Groq client from the GROQ_API_KEY env variable.

        Args:
            model: Groq model name to use (default: llama-3.3-70b-versatile).

        Raises:
            EnvironmentError: If GROQ_API_KEY is not set.
        """
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GROQ_API_KEY environment variable is not set. "
                "Add it to your .env file or export it before running."
            )

        try:
            from groq import Groq  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "The 'groq' package is not installed.  "
                "Run: pip install groq"
            ) from exc

        self._client = Groq(api_key=api_key)
        self._model = model
        self.total_tokens: int = 0
        self.total_cost: float = 0.0

    # ------------------------------------------------------------------
    # LLMProvider interface
    # ------------------------------------------------------------------

    def send(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Send messages to Groq and return a normalised response dict.

        Retries up to ``_MAX_RETRIES`` times on API errors before raising.

        Args:
            messages: Chat history in ``{"role": ..., "content": ...}`` format.
            tools:    Optional list of tool schemas (OpenAI function-calling
                      format).  Passed directly to the Groq API.

        Returns:
            Normalised response dict with keys:
                ``content``, ``tool_call``, ``tokens_used``, ``cost_eur``.

        Raises:
            RuntimeError: If all retry attempts are exhausted.
        """
        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return self._call_api(messages, tools)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY_SECONDS * attempt)

        raise RuntimeError(
            f"Groq API call failed after {_MAX_RETRIES} attempts. "
            f"Last error: {last_error}"
        ) from last_error

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _translate_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert BaseAgent internal message format to Groq/OpenAI format.

        BaseAgent stores:
          {"role": "assistant", "content": "...", "tool_call": {"name": ..., "input": ...}}
          {"role": "tool", "name": ..., "content": ...}

        Groq expects:
          {"role": "assistant", "content": "...", "tool_calls": [{"id": ..., "type": "function", "function": {...}}]}
          {"role": "tool", "tool_call_id": ..., "content": ...}
        """
        translated = []
        last_call_id: str = "call_0"
        call_counter: int = 0

        for msg in messages:
            role = msg.get("role", "")

            if role == "assistant" and msg.get("tool_call"):
                tc = msg["tool_call"]
                name = tc.get("name", "tool")
                arguments = tc.get("input", "{}")
                if not isinstance(arguments, str):
                    import json as _json
                    arguments = _json.dumps(arguments)
                call_counter += 1
                call_id = "call_{}_{}".format(name, call_counter)
                last_call_id = call_id
                translated.append({
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": [{
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }],
                })

            elif role == "tool":
                translated.append({
                    "role": "tool",
                    "tool_call_id": last_call_id,
                    "content": str(msg.get("content", "")),
                })

            else:
                translated.append({
                    "role": role,
                    "content": msg.get("content", ""),
                })

        return translated

    def _call_api(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Make a single API call and parse the response.

        Args:
            messages: Chat messages to send.
            tools:    Optional tool schemas.

        Returns:
            Normalised response dict.
        """
        translated = self._translate_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": translated,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            completion = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            # Model hallucinated a tool not in our schema (e.g. brave_search).
            # The bad call was rejected by Groq BEFORE being returned, so it is
            # NOT in `translated` — the history is already clean.
            # Simply retry with the same history but NO tools so the model is
            # forced to produce a plain-text answer from what it already has.
            if "tool_use_failed" in str(exc) or "tool call validation failed" in str(exc):
                retry_kwargs: dict[str, Any] = {
                    "model": self._model,
                    "messages": translated,  # history already clean
                }
                completion = self._client.chat.completions.create(**retry_kwargs)
            else:
                raise

        return self._parse_response(completion)

    def _parse_response(self, completion: Any) -> dict[str, Any]:
        """Convert a Groq completion object into our standard response dict.

        Args:
            completion: Raw Groq API response object.

        Returns:
            Dict with ``content``, ``tool_call``, ``tokens_used``,
            ``cost_eur``.
        """
        message = completion.choices[0].message

        # --- Text content ---
        content: str = message.content or ""

        # --- Tool call (if any) ---
        import re as _re
        tool_call: dict[str, Any] | None = None
        if message.tool_calls:
            first = message.tool_calls[0]
            tool_call = {
                "name": first.function.name,
                "input": first.function.arguments,  # raw JSON string
            }
        else:
            # Fallback: llama-3.3-70b-versatile sometimes emits tool calls as
            # plain text instead of via the API tool_calls field.
            # Pattern 1: <function=tool_name>{"arg": "val"}</function>
            # Pattern 2: <function>tool_name({"arg": "val"})</function>
            fn_match = _re.search(
                r"<function=(\w+)>\s*(\{.*?\})\s*</function>",
                content,
                _re.DOTALL,
            )
            if not fn_match:
                fn_match = _re.search(
                    r"<function>\s*(\w+)\s*\(?\s*(\{.*?\})\s*\)?\s*</function>",
                    content,
                    _re.DOTALL,
                )
            if fn_match:
                tool_call = {"name": fn_match.group(1).strip(), "input": fn_match.group(2).strip()}
                content = ""

        # --- Token accounting ---
        usage = completion.usage
        input_tokens: int = getattr(usage, "prompt_tokens", 0)
        output_tokens: int = getattr(usage, "completion_tokens", 0)
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
        """Return cumulative token and cost totals for this provider instance.

        Returns:
            Dict with ``total_tokens`` (int) and ``total_cost_eur`` (float).
        """
        return {
            "total_tokens": self.total_tokens,
            "total_cost_eur": round(self.total_cost, 6),
        }
