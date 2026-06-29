"""
Orchestrator — coordinates all specialist agents for the KRYPSOS platform.

Responsibilities:
  - Breaks a high-level task into ordered steps
  - Routes each step to the correct specialist agent
  - Saves full state when an agent raises ApprovalRequired (YELLOW tool)
  - Resumes a paused step after human approval
  - Retries a failed step up to max_retries times
  - Escalates to human when retries are exhausted
  - Ensures QAAgent reviews FinanceAgent / ReportingAgent output
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.base_agent import (
    ApprovalRequired,
    BaseAgent,
    CostLimitReached,
    RedZoneBlocked,
    StepLimitReached,
)


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    WAITING_APPROVAL = "waiting_approval"
    FAILED = "failed"
    SKIPPED = "skipped"


class OrchestrationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    WAITING_APPROVAL = "waiting_approval"  # paused — human must approve
    FAILED = "failed"
    ESCALATED = "escalated"  # exhausted retries — human must decide


@dataclass
class OrchestrationStep:
    step_id: str
    agent_name: str
    task: str
    status: StepStatus = StepStatus.PENDING
    result: str | None = None
    error: str | None = None
    retry_count: int = 0
    approval_state: dict[str, Any] | None = None  # saved agent.pending_approval
    audit_log: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class OrchestrationState:
    run_id: str
    original_task: str
    steps: list[OrchestrationStep]
    status: OrchestrationStatus = OrchestrationStatus.PENDING
    current_step_index: int = 0
    final_result: str | None = None
    escalation_reason: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "original_task": self.original_task,
            "status": self.status.value,
            "current_step_index": self.current_step_index,
            "final_result": self.final_result,
            "escalation_reason": self.escalation_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "steps": [
                {
                    "step_id": s.step_id,
                    "agent_name": s.agent_name,
                    "task": s.task,
                    "status": s.status.value,
                    "result": s.result,
                    "error": s.error,
                    "retry_count": s.retry_count,
                    "has_approval_state": s.approval_state is not None,
                }
                for s in self.steps
            ],
        }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

#: Agents that require QA review after they complete
_QA_REQUIRED_FOR = {"FinanceAgent", "ReportingAgent"}

#: Keyword → agent routing hints (order matters — first match wins)
_ROUTING_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("invoice", "vendor", "document", "pdf", "drive", "file"), "DocumentAgent"),
    (("finance", "payment", "duplicate", "amount", "total", "csv", "export"), "FinanceAgent"),
    (("report", "summary", "weekly", "monthly", "generate"), "ReportingAgent"),
    (("compliance", "deadline", "missing doc", "alert", "telegram"), "ComplianceAgent"),
    (("review", "qa", "verify", "check output", "quality"), "QAAgent"),
    (("email", "send mail", "gmail", "inbox", "reply"), "EmailAgent"),
]


class Orchestrator:
    """Routes tasks across specialist agents with approval/retry/escalation."""

    def __init__(
        self,
        agents: dict[str, BaseAgent],
        max_retries: int = 3,
    ):
        """
        Args:
            agents:       Map of agent_name → BaseAgent instance.
            max_retries:  How many times to retry a failing step before escalating.
        """
        self._agents = agents
        self.max_retries = max_retries

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, task: str) -> OrchestrationState:
        """Start a new orchestration run for the given task.

        Plans steps, executes them in order, handles approval pauses and
        retries, automatically appends a QA step when needed.

        Args:
            task: High-level task description (natural language).

        Returns:
            OrchestrationState describing the final (or paused) state.
        """
        steps = self._plan(task)
        state = OrchestrationState(
            run_id=str(uuid.uuid4()),
            original_task=task,
            steps=steps,
            status=OrchestrationStatus.RUNNING,
        )
        return self._execute(state)

    def resume(
        self,
        state: OrchestrationState,
        approved_tool_result: str,
    ) -> OrchestrationState:
        """Resume a paused orchestration after human approval.

        Reconstructs the agent's message history using the saved snapshot,
        appends the approved tool result, and continues the ReAct loop.

        Args:
            state:                The OrchestrationState returned by ``run()``
                                  or a previous ``resume()`` that is
                                  ``WAITING_APPROVAL``.
            approved_tool_result: The result string to inject for the
                                  YELLOW tool that was awaiting approval.

        Returns:
            Updated OrchestrationState.

        Raises:
            ValueError: If state is not in WAITING_APPROVAL status.
        """
        if state.status != OrchestrationStatus.WAITING_APPROVAL:
            raise ValueError(
                f"Cannot resume: state is '{state.status.value}', not 'waiting_approval'"
            )

        step = state.steps[state.current_step_index]
        if step.approval_state is None:
            raise ValueError(f"Step {step.step_id} has no saved approval state")

        approval = step.approval_state
        agent = self._agents.get(step.agent_name)
        if agent is None:
            step.status = StepStatus.FAILED
            step.error = f"Agent '{step.agent_name}' not registered with orchestrator"
            state.status = OrchestrationStatus.FAILED
            return state

        # Reconstruct messages: snapshot + assistant tool-call msg + tool result
        messages: list[dict[str, Any]] = list(approval["messages_snapshot"])
        messages.append({
            "role": "assistant",
            "content": approval["last_assistant_content"],
            "tool_call": approval["last_tool_call"],
        })
        messages.append({
            "role": "tool",
            "name": approval["tool_name"],
            "content": approved_tool_result,
        })

        step.status = StepStatus.RUNNING
        step.approval_state = None
        state.status = OrchestrationStatus.RUNNING
        state.updated_at = datetime.now(timezone.utc).isoformat()

        try:
            result = agent.run(task=approval["task"], initial_messages=messages)
            step.result = result
            step.status = StepStatus.COMPLETED
            step.audit_log = agent.get_audit_log()
        except ApprovalRequired:
            step.status = StepStatus.WAITING_APPROVAL
            step.approval_state = agent.pending_approval
            state.status = OrchestrationStatus.WAITING_APPROVAL
            state.updated_at = datetime.now(timezone.utc).isoformat()
            return state
        except Exception as exc:
            return self._handle_step_error(state, step, exc)

        # Advance and continue
        state.current_step_index += 1
        return self._execute(state)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _plan(self, task: str) -> list[OrchestrationStep]:
        """Build a list of steps from the task description.

        Uses keyword-based routing. Automatically appends a QA step when
        a FinanceAgent or ReportingAgent step is present.
        """
        task_lower = task.lower()

        # Route to primary agent
        primary_agent = self._route(task_lower)

        steps: list[OrchestrationStep] = [
            OrchestrationStep(
                step_id=str(uuid.uuid4()),
                agent_name=primary_agent,
                task=task,
            )
        ]

        # Auto-append QA step for finance/reporting output
        if primary_agent in _QA_REQUIRED_FOR and "QAAgent" in self._agents:
            steps.append(
                OrchestrationStep(
                    step_id=str(uuid.uuid4()),
                    agent_name="QAAgent",
                    task=f"Review the following output from {primary_agent} and verify accuracy:\n\n{{previous_result}}",
                )
            )

        return steps

    def _route(self, task_lower: str) -> str:
        """Pick the best agent for a task string using keyword hints.

        Falls back to the first registered agent if no hint matches.
        """
        for keywords, agent_name in _ROUTING_HINTS:
            if agent_name in self._agents:
                if any(kw in task_lower for kw in keywords):
                    return agent_name

        # Fallback: first available agent
        return next(iter(self._agents))

    def _execute(self, state: OrchestrationState) -> OrchestrationState:
        """Execute steps from the current index until done, paused, or failed."""
        while state.current_step_index < len(state.steps):
            step = state.steps[state.current_step_index]

            if step.status == StepStatus.SKIPPED:
                state.current_step_index += 1
                continue

            agent = self._agents.get(step.agent_name)
            if agent is None:
                step.status = StepStatus.FAILED
                step.error = f"Agent '{step.agent_name}' not registered with orchestrator"
                state.status = OrchestrationStatus.FAILED
                return state

            # Inject previous step result into task template if needed
            task = step.task
            if state.current_step_index > 0:
                prev_result = state.steps[state.current_step_index - 1].result or ""
                task = task.replace("{previous_result}", prev_result)

            step.status = StepStatus.RUNNING
            state.status = OrchestrationStatus.RUNNING
            state.updated_at = datetime.now(timezone.utc).isoformat()

            try:
                result = agent.run(task)
                step.result = result
                step.status = StepStatus.COMPLETED
                step.audit_log = agent.get_audit_log()
                state.current_step_index += 1

            except ApprovalRequired:
                step.status = StepStatus.WAITING_APPROVAL
                step.approval_state = agent.pending_approval
                state.status = OrchestrationStatus.WAITING_APPROVAL
                state.updated_at = datetime.now(timezone.utc).isoformat()
                return state

            except (StepLimitReached, CostLimitReached, RedZoneBlocked, Exception) as exc:
                return self._handle_step_error(state, step, exc)

        # All steps completed
        last_result = state.steps[-1].result if state.steps else ""
        state.final_result = last_result
        state.status = OrchestrationStatus.COMPLETED
        state.updated_at = datetime.now(timezone.utc).isoformat()
        return state

    def _handle_step_error(
        self,
        state: OrchestrationState,
        step: OrchestrationStep,
        exc: Exception,
    ) -> OrchestrationState:
        """Retry up to max_retries times, then escalate."""
        step.retry_count += 1
        step.error = str(exc)
        state.updated_at = datetime.now(timezone.utc).isoformat()

        if step.retry_count <= self.max_retries:
            # Reset to pending for next _execute pass
            step.status = StepStatus.PENDING
            return self._execute(state)

        # Exhausted retries — escalate
        step.status = StepStatus.FAILED
        state.status = OrchestrationStatus.ESCALATED
        state.escalation_reason = (
            f"Step '{step.agent_name}' failed after {step.retry_count} "
            f"attempts. Last error: {step.error}"
        )
        return state
