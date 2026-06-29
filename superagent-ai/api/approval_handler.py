"""
approval_handler.py — Django↔AI bridge: approval resume.

Called by the Django Celery worker when a human approves or rejects
a YELLOW zone tool call.

Usage from Django Celery task:
    from api.approval_handler import ApprovalHandler, ApprovalRequest, ApprovalResult

    req = ApprovalRequest(
        task_id=str(task.id),
        approved=True,
        tool_name=approval.tool_name,
        tool_input=approval.tool_input,
        resume_snapshot=approval.resume_snapshot,
        agent_type=task.agent.agent_type if task.agent else "email",
        max_steps=task.agent.max_steps if task.agent else 20,
        max_cost=float(task.agent.max_cost_usd) if task.agent else 1.0,
        reviewer_note=approval.reviewer_note,
        original_prompt=task.prompt,
    )
    result = ApprovalHandler().resume(req)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from api.task_handler import _build_agent  # re-exported for patching in tests
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
class ApprovalRequest:
    """Input to ApprovalHandler.resume()."""
    task_id: str
    approved: bool
    tool_name: str
    tool_input: Any                     # the tool call input the human reviewed
    resume_snapshot: dict[str, Any]    # from Approval.resume_snapshot in Django DB
    original_prompt: str
    agent_type: str = "email"
    max_steps: int = 20
    max_cost: float = 1.0
    reviewer_note: str = ""


@dataclass
class ApprovalResult:
    """Output from ApprovalHandler.resume()."""
    task_id: str
    status: str                         # completed | waiting_approval | rejected | failed
    result: str = ""
    error: str = ""
    steps_taken: int = 0
    cost_usd: float = 0.0
    audit_log: list[dict[str, Any]] = field(default_factory=list)
    approval_payload: dict[str, Any] | None = None  # set if another YELLOW tool was hit


# ---------------------------------------------------------------------------
# Approval handler
# ---------------------------------------------------------------------------


class ApprovalHandler:
    """Resumes a paused agent task after human approval/rejection."""

    # The injected tool result that the agent sees when approved.
    # Clearly signals that the action was human-approved.
    _APPROVAL_RESULT_TEMPLATE = (
        "Action approved by human reviewer. "
        "Tool '{tool_name}' executed successfully with the submitted input."
    )

    def resume(self, req: ApprovalRequest) -> ApprovalResult:
        """Resume a paused task.

        Args:
            req: ApprovalRequest with approval decision and resume snapshot.

        Returns:
            ApprovalResult. Check ``status``:
            - ``"completed"``        → task finished.
            - ``"rejected"``         → human rejected; task was cancelled.
            - ``"waiting_approval"`` → another YELLOW tool was hit.
            - ``"failed"``           → agent crashed after resuming.
        """
        logger.info(
            "ApprovalHandler.resume task_id=%s tool=%s approved=%s",
            req.task_id, req.tool_name, req.approved,
        )

        if not req.approved:
            return ApprovalResult(
                task_id=req.task_id,
                status="rejected",
                error=f"Rejected by reviewer: {req.reviewer_note or 'no reason given'}",
            )

        return self._do_resume(req)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _do_resume(self, req: ApprovalRequest) -> ApprovalResult:

        agent = _build_agent(req.agent_type, req.max_steps, req.max_cost)
        if agent is None:
            return ApprovalResult(
                task_id=req.task_id,
                status="failed",
                error=f"Unknown agent type for resume: {req.agent_type}",
            )

        # Reconstruct message history from snapshot
        snapshot = req.resume_snapshot
        messages_snapshot: list[dict] = list(snapshot.get("messages_snapshot", []))
        last_assistant_content: str = snapshot.get("last_assistant_content", "")
        last_tool_call: dict = snapshot.get("last_tool_call", {})

        # Append the assistant's tool-call turn (before approval)
        resume_messages = list(messages_snapshot)
        resume_messages.append({
            "role": "assistant",
            "content": last_assistant_content,
            "tool_call": last_tool_call,
        })
        # Inject the approved result so the LLM continues seamlessly
        approved_result = self._APPROVAL_RESULT_TEMPLATE.format(tool_name=req.tool_name)
        resume_messages.append({
            "role": "tool",
            "name": req.tool_name,
            "content": approved_result,
        })

        try:
            result = agent.run(
                task=req.original_prompt,
                initial_messages=resume_messages,
            )
            cost = agent.get_cost_summary()
            return ApprovalResult(
                task_id=req.task_id,
                status="completed",
                result=result,
                steps_taken=cost.get("total_steps", 0),
                cost_usd=cost.get("total_cost_usd", 0.0),
                audit_log=agent.get_audit_log(),
            )

        except ApprovalRequired:
            cost = agent.get_cost_summary()
            return ApprovalResult(
                task_id=req.task_id,
                status="waiting_approval",
                steps_taken=cost.get("total_steps", 0),
                cost_usd=cost.get("total_cost_usd", 0.0),
                audit_log=agent.get_audit_log(),
                approval_payload=agent.pending_approval,
            )

        except (StepLimitReached, CostLimitReached, RedZoneBlocked) as exc:
            cost = agent.get_cost_summary()
            return ApprovalResult(
                task_id=req.task_id,
                status="failed",
                error=str(exc),
                steps_taken=cost.get("total_steps", 0),
                cost_usd=cost.get("total_cost_usd", 0.0),
                audit_log=agent.get_audit_log(),
            )

        except Exception as exc:
            logger.exception("Resume failed for task_id=%s", req.task_id)
            return ApprovalResult(
                task_id=req.task_id,
                status="failed",
                error=f"Resume error: {exc}",
            )
