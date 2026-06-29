"""
task_handler.py — Django↔AI bridge: task execution.

Called by the Django Celery worker (apps/tasks/tasks.py).
Pure Python — no Django ORM imports here, so this module is
independently testable without a Django environment.

Usage from Django Celery task:
    from api.task_handler import TaskHandler, TaskRequest, TaskResult

    req = TaskRequest(
        task_id=str(task.id),
        prompt=task.prompt,
        agent_type=task.agent.agent_type if task.agent else "auto",
        max_steps=task.agent.max_steps if task.agent else 20,
        max_cost=float(task.agent.max_cost_usd) if task.agent else 1.0,
    )
    result = TaskHandler().execute(req)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from core.base_agent import (
    ApprovalRequired,
    CostLimitReached,
    RedZoneBlocked,
    StepLimitReached,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request / Result data classes
# ---------------------------------------------------------------------------


@dataclass
class TaskRequest:
    """Input to TaskHandler.execute()."""
    task_id: str
    prompt: str
    agent_type: str = "auto"       # email, finance, document, reporting, compliance, qa, auto
    max_steps: int = 20
    max_cost: float = 1.0
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    """Output from TaskHandler.execute()."""
    task_id: str
    status: str                     # completed | waiting_approval | failed
    result: str = ""
    error: str = ""
    steps_taken: int = 0
    cost_usd: float = 0.0
    audit_log: list[dict[str, Any]] = field(default_factory=list)
    approval_payload: dict[str, Any] | None = None   # set when status == waiting_approval


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def _build_llm():
    """Build LLM provider from environment."""
    try:
        from core.llm.groq_provider import GroqProvider
        return GroqProvider()
    except Exception as exc:
        raise EnvironmentError(f"Cannot build LLM provider: {exc}") from exc


def _build_agent(agent_type: str, max_steps: int, max_cost: float):
    """Instantiate the correct agent for the given type string."""
    llm = _build_llm()

    agent_type = agent_type.lower()

    if agent_type == "email":
        from agents.email_agent import EmailAgent
        agent = EmailAgent(llm_provider=llm)

    elif agent_type == "document":
        from agents.document_agent import DocumentAgent
        agent = DocumentAgent(llm_provider=llm)

    elif agent_type == "finance":
        from agents.finance_agent import FinanceAgent
        agent = FinanceAgent(llm_provider=llm)

    elif agent_type == "reporting":
        from agents.reporting_agent import ReportingAgent
        agent = ReportingAgent(llm_provider=llm)

    elif agent_type == "compliance":
        from agents.compliance_agent import ComplianceAgent
        agent = ComplianceAgent(llm_provider=llm)

    elif agent_type == "qa":
        from agents.qa_agent import QAAgent
        agent = QAAgent(llm_provider=llm)

    else:
        # "auto" — route by Orchestrator
        return None

    agent.max_steps = max_steps
    agent.max_cost = max_cost
    return agent


# ---------------------------------------------------------------------------
# Task handler
# ---------------------------------------------------------------------------


class TaskHandler:
    """Executes a task request and returns a structured result.

    Handles:
    - Single-agent execution (agent_type specified)
    - Orchestrated multi-agent execution (agent_type="auto")
    - ApprovalRequired pause — serialises full resume snapshot
    - Cost/step/red-zone failures
    """

    def execute(self, req: TaskRequest) -> TaskResult:
        """Run a task and return the result.

        Args:
            req: TaskRequest with prompt, agent type, and limits.

        Returns:
            TaskResult. Check ``status`` field:
            - ``"completed"``        → ``result`` contains the final answer.
            - ``"waiting_approval"`` → ``approval_payload`` has the snapshot.
            - ``"failed"``           → ``error`` has the reason.
        """
        logger.info("TaskHandler.execute task_id=%s agent_type=%s", req.task_id, req.agent_type)

        try:
            if req.agent_type.lower() == "auto":
                return self._run_orchestrated(req)
            else:
                return self._run_single(req)

        except Exception as exc:
            logger.exception("Unexpected error in TaskHandler.execute task_id=%s", req.task_id)
            return TaskResult(
                task_id=req.task_id,
                status="failed",
                error=f"Internal error: {exc}",
            )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _run_single(self, req: TaskRequest) -> TaskResult:
        """Run a single specialist agent."""
        agent = _build_agent(req.agent_type, req.max_steps, req.max_cost)
        if agent is None:
            return TaskResult(
                task_id=req.task_id,
                status="failed",
                error=f"Unknown agent type: {req.agent_type}",
            )

        try:
            result = agent.run(req.prompt)
            cost = agent.get_cost_summary()
            return TaskResult(
                task_id=req.task_id,
                status="completed",
                result=result,
                steps_taken=cost.get("total_steps", 0),
                cost_usd=cost.get("total_cost_usd", 0.0),
                audit_log=agent.get_audit_log(),
            )

        except ApprovalRequired:
            cost = agent.get_cost_summary()
            return TaskResult(
                task_id=req.task_id,
                status="waiting_approval",
                steps_taken=cost.get("total_steps", 0),
                cost_usd=cost.get("total_cost_usd", 0.0),
                audit_log=agent.get_audit_log(),
                approval_payload=agent.pending_approval,
            )

        except (StepLimitReached, CostLimitReached, RedZoneBlocked) as exc:
            cost = agent.get_cost_summary()
            return TaskResult(
                task_id=req.task_id,
                status="failed",
                error=str(exc),
                steps_taken=cost.get("total_steps", 0),
                cost_usd=cost.get("total_cost_usd", 0.0),
                audit_log=agent.get_audit_log(),
            )

    def _run_orchestrated(self, req: TaskRequest) -> TaskResult:
        """Run via Orchestrator for automatic agent routing."""
        from agents.orchestrator import Orchestrator, OrchestrationStatus

        agents: dict = {}
        for name, atype in [
            ("EmailAgent", "email"),
            ("DocumentAgent", "document"),
            ("FinanceAgent", "finance"),
            ("ReportingAgent", "reporting"),
            ("ComplianceAgent", "compliance"),
            ("QAAgent", "qa"),
        ]:
            try:
                a = _build_agent(atype, req.max_steps, req.max_cost)
                if a:
                    agents[name] = a
            except Exception:
                pass  # agent unavailable (missing env vars) — skip it

        if not agents:
            return TaskResult(
                task_id=req.task_id,
                status="failed",
                error="No agents available — check environment configuration",
            )

        orch = Orchestrator(agents=agents)
        state = orch.run(req.prompt)

        if state.status == OrchestrationStatus.COMPLETED:
            return TaskResult(
                task_id=req.task_id,
                status="completed",
                result=state.final_result or "",
            )

        if state.status == OrchestrationStatus.WAITING_APPROVAL:
            current_step = state.steps[state.current_step_index]
            return TaskResult(
                task_id=req.task_id,
                status="waiting_approval",
                approval_payload={
                    "orchestration_state": state.to_dict(),
                    "step_id": current_step.step_id,
                    "agent_name": current_step.agent_name,
                    **(current_step.approval_state or {}),
                },
            )

        # FAILED or ESCALATED
        return TaskResult(
            task_id=req.task_id,
            status="failed",
            error=state.escalation_reason or "Orchestration failed",
        )
