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
_MAX_RETRIES: int = 2
_RETRY_DELAY_SECONDS: float = 2.0
_REQUEST_TIMEOUT_SECONDS: float = 45.0
_MODEL: str = "llama-3.3-70b-versatile"
_MAX_RATE_LIMIT_RETRIES: int = 5          # wait up to 5×62 s ≈ 5 min for token bucket to refill
_RATE_LIMIT_WAIT_SECONDS: float = 62.0   # Groq bucket refills every 60 s; add 2 s buffer

_RATE_LIMIT_MESSAGE = (
    "⚠️ The AI is temporarily busy due to high usage (Groq rate limit reached). "
    "Please wait about 1 minute and try again."
)


class GroqRateLimitError(RuntimeError):
    """Raised when Groq returns a 429 / quota-exhaustion error."""
    pass


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "rate_limit_exceeded" in msg or "rate limit" in msg or "quota" in msg


class GroqProvider(LLMProvider):
    """LLM provider backed by the Groq API.

    Attributes:
        total_tokens: Cumulative tokens used across all calls this session.
        total_cost:   Cumulative cost (EUR) across all calls this session.
    """

    def __init__(self, model: str = _MODEL) -> None:
        """Initialise the Groq client.

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

        self._client = Groq(api_key=api_key, timeout=_REQUEST_TIMEOUT_SECONDS, max_retries=0)
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
        force_tool: bool = False,
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
                            "Groq rate limit hit — waiting %.0fs before retry %d/%d",
                            _RATE_LIMIT_WAIT_SECONDS, rate_limit_waits, _MAX_RATE_LIMIT_RETRIES,
                        )
                        time.sleep(_RATE_LIMIT_WAIT_SECONDS)
                        continue  # retry without counting as a normal failure
                    raise GroqRateLimitError(_RATE_LIMIT_MESSAGE) from exc
                normal_attempts += 1
                last_error = exc
                if normal_attempts < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY_SECONDS * normal_attempts)
                else:
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
                # Use a counter to guarantee unique IDs even for repeated tool calls
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
                # system / user — pass through, strip unknown keys
                translated.append({
                    "role": role,
                    "content": msg.get("content", ""),
                })

        return translated

    def _call_api(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        force_tool: bool = False,
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
            # force_tool=True → model MUST call a tool (no plain-text response allowed)
            kwargs["tool_choice"] = "required" if force_tool else "auto"

        try:
            completion = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            # Model used wrong parameter names — Groq rejected the tool call.
            if "tool_use_failed" in str(exc) or "tool call validation failed" in str(exc):
                # Try to salvage the tool call from the error's failed_generation
                salvaged = GroqProvider._salvage_failed_tool_call(exc)
                if salvaged is not None:
                    return {"content": "", "tool_call": salvaged, "tokens_used": 0, "cost_eur": 0.0}
                # Fallback: retry with tool_choice=auto
                retry_kwargs: dict[str, Any] = {
                    "model": self._model,
                    "messages": translated,
                }
                if tools:
                    retry_kwargs["tools"] = tools
                    retry_kwargs["tool_choice"] = "auto"
                completion = self._client.chat.completions.create(**retry_kwargs)
            else:
                raise

        return self._parse_response(completion)

    @staticmethod
    def _salvage_failed_tool_call(exc: Exception) -> "dict | None":
        """Try to extract a valid tool call from a tool_use_failed error."""
        import re as _re, json as _json

        # Extract failed_generation string
        raw = ""
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            try:
                raw = body["error"]["failed_generation"]
            except (KeyError, TypeError):
                pass
        if not raw:
            m = _re.search(
                r'"failed_generation"\s*:\s*"((?:[^"\\]|\\.)*)"\'',
                str(exc),
            )
            if m:
                try:
                    raw = _json.loads('"' + m.group(1) + '"')
                except Exception:
                    raw = m.group(1)
        if not raw:
            return None

        # Pattern 1: <function=name>{...}</function>
        m1 = _re.search(r"<function=(\w+)>\s*(\{.*?\})\s*</function>", raw, _re.DOTALL)
        if m1:
            return {"name": m1.group(1).strip(), "input": m1.group(2).strip()}

        # Pattern 2: name({...}) or name()
        m2 = _re.search(r"(\w+)\s*\(\s*(\{.*?\})?\s*\)", raw, _re.DOTALL)
        if m2:
            return {"name": m2.group(1).strip(), "input": (m2.group(2) or "{}").strip()}

        # Pattern 3: raw JSON with name/tool/function key
        try:
            obj = _json.loads(raw)
            if isinstance(obj, dict):
                name = obj.get("name") or obj.get("tool") or obj.get("function")
                args = obj.get("arguments") or obj.get("parameters") or obj.get("input") or {}
                if name:
                    return {
                        "name": str(name),
                        "input": args if isinstance(args, str) else _json.dumps(args),
                    }
        except Exception:
            pass

        return None

    def _parse_response(self, completion: Any) -> dict[str, Any]:
        """Convert a Groq completion object into our standard response dict.

        Args:
            completion: Raw Groq API response object.

        Returns:
            Dict with ``content``, ``tool_call``, ``tokens_used``,
            ``cost_eur``.
        """
        import re as _re

        message = completion.choices[0].message

        # --- Text content ---
        content: str = message.content or ""

        # --- Tool call (if any) ---
        tool_call: dict[str, Any] | None = None
        if message.tool_calls:
            first = message.tool_calls[0]
            tool_call = {
                "name": first.function.name,
                "input": first.function.arguments,  # raw JSON string
            }
        else:
            # Fallback: some llama variants emit tool calls as text rather than
            # via the API tool_calls field.  Detect and parse them so the agent
            # loop can execute the tool instead of treating this as a final answer.
            #
            # Pattern 1: <function=tool_name>{"arg": "val"}</function>  ← llama-3.3-70b-versatile
            # Pattern 2: <function>tool_name({"arg": "val"})</function>
            # Pattern 3: <function_calls><invoke><tool_name>t</tool_name>
            #              <parameters>{"arg": "val"}</parameters></invoke></function_calls>

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
            if not fn_match:
                # Try the XML-style format
                fn_match = _re.search(
                    r"<invoke>\s*<tool_name>\s*(\w+)\s*</tool_name>\s*"
                    r"<parameters>\s*(\{.*?\})\s*</parameters>",
                    content,
                    _re.DOTALL,
                )

            if not fn_match:
                # Pattern 4: bare Python-style call inside a code block
                # e.g. generate_content({"title": "...", "doc_type": "..."})
                # or   generate_content(task="...", sections=[...])
                fn_match = _re.search(
                    r"(\w+)\s*\(\s*(\{[^)]+\})\s*\)",
                    content,
                    _re.DOTALL,
                )

            if not fn_match:
                # Pattern 5: bare no-argument tool call, e.g. `summarize_emails()`
                no_arg_match = _re.fullmatch(
                    r"\s*`{0,3}\s*(\w+)\s*\(\s*\)\s*`{0,3}\s*",
                    content,
                    _re.DOTALL,
                )
                if no_arg_match:
                    tool_call = {"name": no_arg_match.group(1).strip(), "input": "{}"}
                    content = ""

            if fn_match:
                fn_name = fn_match.group(1).strip()
                fn_args = fn_match.group(2).strip()
                tool_call = {"name": fn_name, "input": fn_args}
                # Clear content so it is not surfaced as a final answer
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
