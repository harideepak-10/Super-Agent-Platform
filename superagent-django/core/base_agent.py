"""
Base agent — the core ReAct loop for the Super Agent platform.

BaseAgent drives the think → act → observe loop:
  1. Send the task (and accumulated message history) to the LLM.
  2. If the LLM chose a tool, check the tool's zone:
       GREEN  → run the tool, append the result, loop.
       YELLOW → raise ApprovalRequired and pause execution.
       RED    → raise RedZoneBlocked immediately.
  3. If the LLM produced no tool call, the task is complete.
  4. After every step, check cost and step-count limits.
  5. Log every action to a structured audit log.

All custom exceptions are defined in this module so they can be caught
at higher layers without circular imports.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .llm.base import LLMProvider
from .memory.working_memory import WorkingMemory
from .tools.base_tool import BaseTool, ToolZone


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class ApprovalRequired(Exception):
    """Raised when an agent tries to call a YELLOW zone tool.

    The caller (human-in-the-loop layer or orchestrator) must inspect
    ``tool_name`` and ``tool_input``, decide whether to approve, and
    either re-invoke the agent with the pre-approved result or abort.

    Attributes:
        tool_name:  Name of the tool awaiting approval.
        tool_input: Raw input string the agent wants to pass to the tool.
        message:    Human-readable explanation.
    """

    def __init__(self, tool_name: str, tool_input: str, message: str = "") -> None:
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.message = message or (
            f"Tool '{tool_name}' requires human approval before execution."
        )
        super().__init__(self.message)


class RedZoneBlocked(Exception):
    """Raised when an agent tries to call a RED zone tool.

    RED zone tools are never executed by the agent.  A human must
    perform the action manually.

    Attributes:
        tool_name: Name of the blocked tool.
        message:   Human-readable explanation.
    """

    def __init__(self, tool_name: str, message: str = "") -> None:
        self.tool_name = tool_name
        self.message = message or (
            f"Tool '{tool_name}' is RED zone — agent execution is not permitted."
        )
        super().__init__(self.message)


class CostLimitReached(Exception):
    """Raised when cumulative LLM cost exceeds ``max_cost``.

    Attributes:
        cost_so_far: Cumulative cost (EUR) at the point of the limit hit.
        max_cost:    The configured cost ceiling (EUR).
    """

    def __init__(self, cost_so_far: float, max_cost: float) -> None:
        self.cost_so_far = cost_so_far
        self.max_cost = max_cost
        super().__init__(
            f"Cost limit reached: €{cost_so_far:.6f} exceeds max €{max_cost:.6f}."
        )


class StepLimitReached(Exception):
    """Raised when the agent loop exceeds ``max_steps``.

    Attributes:
        steps_taken: Number of steps completed so far.
        max_steps:   The configured step ceiling.
    """

    def __init__(self, steps_taken: int, max_steps: int) -> None:
        self.steps_taken = steps_taken
        self.max_steps = max_steps
        super().__init__(
            f"Step limit reached: {steps_taken} steps taken, max is {max_steps}."
        )


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------


class BaseAgent:
    """Core agent loop with zone-based tool safety and audit logging.

    Subclasses customise the agent by:
      - Overriding ``_system_prompt()`` to provide a role-specific
        system message.
      - Passing a tailored tool list to ``__init__``.
      - Adjusting ``max_steps`` and ``max_cost`` for the use case.

    Attributes:
        name:       Human-readable agent identifier.
        task_id:    Unique ID for the current task (auto-generated if
                    not supplied).
        audit_log:  List of structured audit log entries accumulated
                    during the current run.
    """

    def __init__(
        self,
        name: str,
        llm_provider: LLMProvider,
        tools: list[BaseTool],
        max_steps: int = 20,
        max_cost: float = 1.0,
        task_id: str | None = None,
    ) -> None:
        """Initialise the agent.

        Args:
            name:         Human-readable name (e.g. "EmailAgent").
            llm_provider: Concrete LLMProvider instance to use for
                          all LLM calls.
            tools:        List of BaseTool instances available to the
                          agent.
            max_steps:    Maximum number of loop iterations before
                          StepLimitReached is raised.
            max_cost:     Maximum cumulative LLM cost (EUR) before
                          CostLimitReached is raised.
            task_id:      Optional external task identifier.  A UUID is
                          auto-generated when not supplied.
        """
        self.name = name
        self._llm = llm_provider
        self._tools: dict[str, BaseTool] = {t.name: t for t in tools}
        self.max_steps = max_steps
        self.max_cost = max_cost
        self.task_id = task_id or str(uuid.uuid4())

        self._memory = WorkingMemory()
        self.audit_log: list[dict[str, Any]] = []
        self._step: int = 0
        self._cost_so_far: float = 0.0
        self._hallucination_reprompts: int = 0

        # Populated just before ApprovalRequired is raised so the API
        # layer can save full state and resume after human approval.
        self.pending_approval: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        task: str,
        initial_messages: list[dict[str, Any]] | None = None,
    ) -> str:
        """Execute the agent loop for the given task.

        Args:
            task:             A natural-language description of what the
                              agent should accomplish.  Used for logging;
                              ignored when ``initial_messages`` is supplied.
            initial_messages: When provided, the agent resumes from this
                              pre-built message history instead of starting
                              fresh.  Used by the API approval-resume flow:
                              the saved snapshot + approved tool result are
                              passed here so the loop continues seamlessly.

        Returns:
            The final text response from the LLM once no more tool
            calls are needed.

        Raises:
            ApprovalRequired:   Agent wants to call a YELLOW zone tool.
            RedZoneBlocked:     Agent tried to call a RED zone tool.
            CostLimitReached:   Cumulative cost exceeded ``max_cost``.
            StepLimitReached:   Loop ran more than ``max_steps`` times.
        """
        self._reset_run_state()

        if initial_messages is not None:
            # Resume mode — continue from a saved message snapshot
            messages: list[dict[str, Any]] = list(initial_messages)
            self._log("task_resumed", {"task": task, "message_count": len(messages)})
        else:
            # Fresh start — build message list from scratch
            self._log("task_started", {"task": task})
            messages = []
            system_prompt = self._system_prompt()
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": task})

        tool_schemas = [t.to_schema() for t in self._tools.values()]

        def _compress_history(msgs: list[dict]) -> list[dict]:
            """Truncate old tool results to save tokens.

            Keeps the last 2 tool results at full length.
            Older ones are trimmed to 500 chars so the LLM still has context
            without burning tokens on large email bodies / JSON blobs.
            """
            _MAX_OLD_RESULT = 500
            tool_indices = [i for i, m in enumerate(msgs) if m.get("role") == "tool"]
            to_trim = tool_indices[:-2]  # keep last 2 intact
            compressed = []
            for i, m in enumerate(msgs):
                if i in to_trim and len(m.get("content", "")) > _MAX_OLD_RESULT:
                    m = {**m, "content": m["content"][:_MAX_OLD_RESULT] + "… [truncated]"}
                compressed.append(m)
            return compressed

        try:
            while True:
                self._step += 1
                self._check_limits()

                # --- Step 1: Call the LLM ---
                messages = _compress_history(messages)
                self._log("llm_called", {"step": self._step, "message_count": len(messages)})
                # Force a tool call when no tool has run yet — prevents the model
                # from skipping straight to a hallucinated final answer.
                tools_used_so_far = any(m.get("role") == "tool" for m in messages)
                force_tool = bool(tool_schemas) and not tools_used_so_far
                response = self._llm.send(messages, tool_schemas, force_tool=force_tool)
                self._cost_so_far += response.get("cost_eur", 0.0)

                tool_call = response.get("tool_call")
                content = response.get("content", "")

                # --- Step 2: No tool call → task complete (or push back if no tools used yet) ---
                if not tool_call:
                    # Guard: if no tool has run yet the model is hallucinating success.
                    # Re-prompt up to 3 times with increasingly explicit instructions.
                    tools_used_so_far = any(
                        m.get("role") == "tool" for m in messages
                    )
                    if tool_schemas and not tools_used_so_far and self._hallucination_reprompts < 3:
                        self._hallucination_reprompts += 1
                        self._log(
                            "hallucination_guard",
                            {
                                "step": self._step,
                                "reprompt": self._hallucination_reprompts,
                                "content_preview": content[:120],
                                "note": "Model returned text without calling any tool — re-prompting",
                            },
                        )
                        # Build list of available tool names for the model
                        tool_names = ", ".join(
                            s["function"]["name"]
                            for s in tool_schemas
                            if "function" in s
                        )
                        messages.append({"role": "assistant", "content": content or ""})
                        messages.append({
                            "role": "user",
                            "content": (
                                "ERROR: You wrote text but called NO tools. Nothing was executed.\n\n"
                                "Rules:\n"
                                "- Do NOT write Python code or pseudocode.\n"
                                "- Do NOT describe what you will do.\n"
                                "- You MUST call one of these tools right now using the tool-calling mechanism: "
                                f"{tool_names}\n\n"
                                "For document creation tasks, call generate_content with JSON like this:\n"
                                '{"title": "Report Title", "doc_type": "report", "prompt": "describe what to write"}\n\n'
                                "Call the tool NOW. Your response must be a tool call, not text."
                            ),
                        })
                        continue

                    content = self._clean_result(content)

                    # Grounding check: does the answer match what actually ran?
                    inconsistency = self._detect_ungrounded_claim(content)
                    if inconsistency:
                        # One corrective retry
                        self._log("grounding_retry", {"step": self._step, "reason": inconsistency})
                        messages.append({"role": "assistant", "content": content})
                        messages.append({
                            "role": "user",
                            "content": (
                                f"Your answer appears inconsistent with what actually happened: {inconsistency} "
                                "Please correct your response to accurately reflect what was done."
                            ),
                        })
                        response2 = self._llm.send(messages, tool_schemas)
                        content2 = self._clean_result(response2.get("content", "") or content)
                        # If still ungrounded, prepend a disclaimer but don't loop
                        if self._detect_ungrounded_claim(content2):
                            self._log("grounding_warning", {"step": self._step})
                            content2 = "⚠️ " + content2
                        content = content2

                    self._log(
                        "task_completed",
                        {"result": content, "total_steps": self._step},
                    )
                    return content

                tool_name = tool_call.get("name", "")
                tool_input = tool_call.get("input", "")

                # --- Step 3: Zone check ---
                tool = self._tools.get(tool_name)
                if tool is None:
                    error_msg = f"Unknown tool requested by LLM: '{tool_name}'"
                    self._log("error", {"error": error_msg, "step": self._step})
                    # Inform the LLM so it can recover
                    messages.append(
                        {"role": "assistant", "content": content or "", "tool_call": tool_call}
                    )
                    messages.append(
                        {"role": "tool", "name": tool_name, "content": error_msg}
                    )
                    continue

                zone = tool.zone

                if zone == ToolZone.RED:
                    self._log(
                        "error",
                        {
                            "event": "red_zone_blocked",
                            "tool_name": tool_name,
                            "step": self._step,
                        },
                    )
                    raise RedZoneBlocked(tool_name)

                if zone == ToolZone.YELLOW:
                    self._log(
                        "approval_needed",
                        {
                            "tool_name": tool_name,
                            "tool_input": tool_input,
                            "step": self._step,
                        },
                    )
                    # Snapshot everything the API needs to resume after approval.
                    # messages_snapshot is the state BEFORE this assistant turn;
                    # the API appends the assistant msg + tool result on resume.
                    self.pending_approval = {
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "messages_snapshot": list(messages),   # shallow copy
                        "last_assistant_content": content or "",
                        "last_tool_call": tool_call,
                        "task": task,
                        "audit_log_snapshot": list(self.audit_log),
                    }
                    raise ApprovalRequired(tool_name, tool_input)

                # zone == GREEN: run the tool immediately
                self._log(
                    "tool_called",
                    {"tool_name": tool_name, "tool_input": tool_input, "step": self._step},
                )
                try:
                    result = tool.run(tool_input)
                except Exception as exc:  # noqa: BLE001
                    result = f"Tool error: {exc}"
                    self._log(
                        "error",
                        {"tool_name": tool_name, "error": str(exc), "step": self._step},
                    )

                self._log(
                    "tool_result",
                    {"tool_name": tool_name, "result": result, "step": self._step},
                )

                # Append assistant message and tool result to history
                messages.append(
                    {"role": "assistant", "content": content or "", "tool_call": tool_call}
                )
                messages.append(
                    {"role": "tool", "name": tool_name, "content": result}
                )

        except (ApprovalRequired, RedZoneBlocked, CostLimitReached, StepLimitReached):
            raise  # propagate control-flow exceptions unchanged
        except Exception as exc:  # noqa: BLE001
            self._log("error", {"error": str(exc), "step": self._step})
            raise
        finally:
            # Always clear working memory when task ends (success or error)
            self._memory.clear()

    def get_audit_log(self) -> list[dict[str, Any]]:
        """Return the full audit log for the most recent run.

        Returns:
            List of audit log entry dicts, each containing:
            ``timestamp``, ``event_type``, ``details``,
            ``step_number``, ``cost_so_far``.
        """
        return list(self.audit_log)

    def get_cost_summary(self) -> dict[str, Any]:
        """Return token and cost totals for the most recent run.

        Returns:
            Dict with ``total_cost_eur`` (float) and
            ``total_steps`` (int).
        """
        return {
            "total_cost_eur": round(self._cost_so_far, 6),
            "total_steps": self._step,
        }

    # ------------------------------------------------------------------
    # Overrideable hooks
    # ------------------------------------------------------------------

    def _invoked_tool_names(self) -> set[str]:
        """Return the set of tool names actually called this run (from audit log)."""
        return {
            e["details"].get("tool_name", "")
            for e in self.audit_log
            if e.get("event_type") == "tool_called"
        }

    def _detect_ungrounded_claim(self, content: str) -> str | None:
        """Return a description of the inconsistency if content is ungrounded, else None."""
        import re as _re
        invoked = self._invoked_tool_names()
        lower = content.lower()

        # Pattern A: answer names a tool that was never actually called
        _TOOL_MENTIONS = [
            ("read_email",         ["read_email", "'read_email'", "read_emails"]),
            ("search_emails",      ["search_emails", "'search_emails'"]),
            ("summarize_emails",   ["summarize_emails", "summarised", "summarized"]),
            ("send_email",         ["send_email", "email sent", "sent the email"]),
            ("create_gmail_draft", ["create_gmail_draft", "draft saved", "saved to drafts"]),
            ("reply_to_email",     ["reply_to_email", "replied to"]),
            ("detect_follow_up",   ["detect_follow_up", "follow-up detected"]),
        ]
        for tool_name, phrases in _TOOL_MENTIONS:
            if any(p in lower for p in phrases) and tool_name not in invoked:
                return f"Answer mentions '{tool_name}' but that tool was never called."

        # Pattern B: answer denies doing anything while a mutating tool did run
        _MUTATING = {"send_email", "create_gmail_draft", "reply_to_email",
                     "forward_email", "schedule_email", "mark_as_read", "create_draft"}
        _DENIAL_PHRASES = ["no emails", "nothing to", "could not find", "unable to",
                           "i don't have", "no results", "nothing was"]
        if invoked & _MUTATING and any(p in lower for p in _DENIAL_PHRASES):
            ran = ", ".join(invoked & _MUTATING)
            return f"Answer denies doing anything but mutating tool(s) ran: {ran}."

        # Pattern C: fabricated / placeholder data — model invented fake results
        _PLACEHOLDER = [
            r"subject\s*\d+\s*[-–]\s*sender\s*\d+",   # "Subject 1 - Sender 1"
            r"\bsender\s*\d+\b",                        # "Sender 1"
            r"\bsubject\s*\d+\b",                       # "Subject 1"
            r"assuming the response",                   # "Assuming the response is..."
            r"assuming.*emails.*count",                 # "Assuming {"emails":[...], "count":5}"
            r"simple example.*summary",                 # "simple example ... summary"
            r"for example.*you can print",              # "For example, you can print"
            r"i don.t have the actual email content",   # "I don't have the actual email content"
            r"natural language processing techniques to analyze",  # generic NLP suggestion
        ]
        for pattern in _PLACEHOLDER:
            if _re.search(pattern, lower):
                return (
                    "Your response contains fabricated placeholder data or describes what the "
                    "tool response might look like instead of using real data. "
                    "You MUST call the read_emails or search_emails tool and use the ACTUAL result."
                )

        return None

    def _clean_result(self, text: str) -> str:
        """Strip narration lines the model sometimes includes in its final answer."""
        import re
        _NARRATION = re.compile(
            r"^.*(i will use|i am going to|i('ll| will) now|please wait|"
            r"the tool has finished|i have (successfully|now)|"
            r"i('m| am) (now |currently )?(using|reading|fetching|checking|calling)|"
            r"let me (use|read|fetch|check|call|now)).*$",
            re.IGNORECASE,
        )
        lines = text.splitlines()
        cleaned = [ln for ln in lines if not _NARRATION.match(ln.strip())]
        # Remove leading/trailing blank lines left after stripping
        result = "\n".join(cleaned).strip()
        return result or text  # fallback to original if everything was stripped

    def _system_prompt(self) -> str:
        """Return the system prompt sent to the LLM at the start of every task.

        Subclasses should override this to provide a role-specific prompt.

        Returns:
            System prompt string, or empty string for no system message.
        """
        return ""

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _reset_run_state(self) -> None:
        """Reset per-run counters and log before starting a new task."""
        self._step = 0
        self._cost_so_far = 0.0
        self._hallucination_reprompts = 0
        self.audit_log = []
        self.pending_approval = None
        self._memory.clear()

    def _check_limits(self) -> None:
        """Raise the appropriate exception if a limit has been exceeded.

        Raises:
            StepLimitReached:  If ``self._step > self.max_steps``.
            CostLimitReached:  If ``self._cost_so_far > self.max_cost``.
        """
        if self._step > self.max_steps:
            self._log(
                "step_limit_reached",
                {"steps_taken": self._step, "max_steps": self.max_steps},
            )
            raise StepLimitReached(self._step, self.max_steps)

        if self._cost_so_far > self.max_cost:
            self._log(
                "cost_limit_reached",
                {"cost_so_far": self._cost_so_far, "max_cost": self.max_cost},
            )
            raise CostLimitReached(self._cost_so_far, self.max_cost)

    def _log(self, event_type: str, details: dict[str, Any]) -> None:
        """Append a structured entry to the audit log.

        Args:
            event_type: One of the canonical event type strings defined
                        in the class docstring.
            details:    Arbitrary dict of context for this event.
        """
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "details": details,
            "step_number": self._step,
            "cost_so_far": round(self._cost_so_far, 6),
        }
        self.audit_log.append(entry)

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"name={self.name!r} "
            f"tools={list(self._tools.keys())} "
            f"max_steps={self.max_steps} "
            f"max_cost={self.max_cost}>"
        )
