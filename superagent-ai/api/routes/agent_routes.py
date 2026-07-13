"""
Agent task routes.

POST /agent/run                  — start a new agent task
POST /agent/approve/{session_id} — approve or deny a YELLOW zone tool call
GET  /agent/sessions             — list all sessions
GET  /agent/sessions/{session_id}— get a single session summary
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import get_current_user
from api.models import (
    ApprovalNeeded,
    ApproveRequest,
    ApproveResponse,
    RunRequest,
    RunResponse,
    SessionSummary,
)
from api.session import Session, create_session, get_session, list_sessions
from core.base_agent import ApprovalRequired, CostLimitReached, StepLimitReached

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["Agent"])


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def _build_email_agent():
    """Build a production EmailAgent with GroqProvider.

    Raises:
        HTTPException 503: If GROQ_API_KEY is missing.
    """
    try:
        from agents.email_agent import EmailAgent
        from core.llm.groq_provider import GroqProvider
        return EmailAgent(llm_provider=GroqProvider())
    except EnvironmentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Agent not available: {exc}",
        )


def _build_agent_for(agent_type: str):
    """Return the appropriate agent instance for the given type.

    Args:
        agent_type: e.g. ``"email"``

    Returns:
        Configured agent instance.
    """
    if agent_type == "email":
        return _build_email_agent()
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unknown agent type: '{agent_type}'. Supported: email",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/run",
    response_model=RunResponse,
    status_code=status.HTTP_200_OK,
    summary="Run an agent task",
    description=(
        "Start a new agent task.  The agent runs until it either completes "
        "the task or encounters a YELLOW zone tool that requires human "
        "approval.  If approval is needed, the response status will be "
        "'pending_approval' and you must call POST /agent/approve/{session_id}."
    ),
)
def run_task(
    request: RunRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> RunResponse:
    """Execute an agent task and return the result or approval request.

    Args:
        request:      Task description and agent type.
        current_user: JWT payload injected by auth dependency.

    Returns:
        RunResponse with status, result, and optional approval details.
    """
    session = create_session(request.task)
    agent = _build_agent_for(request.agent)

    try:
        result = agent.run(request.task)
        summary = agent.get_cost_summary()
        session.mark_completed(
            result=result,
            audit_log=agent.get_audit_log(),
            cost_eur=summary["total_cost_eur"],
            steps_taken=summary["total_steps"],
        )
        return RunResponse(
            session_id=session.session_id,
            status="completed",
            result=result,
            cost_eur=summary["total_cost_eur"],
            steps_taken=summary["total_steps"],
        )

    except ApprovalRequired as exc:
        pa = agent.pending_approval or {}
        summary = agent.get_cost_summary()
        session.mark_pending_approval(
            tool_name=exc.tool_name,
            tool_input=exc.tool_input,
            messages_snapshot=pa.get("messages_snapshot", []),
            assistant_content=pa.get("last_assistant_content", ""),
            tool_call=pa.get("last_tool_call", {}),
            audit_log=agent.get_audit_log(),
            cost_eur=summary["total_cost_eur"],
            steps_taken=summary["total_steps"],
        )
        return RunResponse(
            session_id=session.session_id,
            status="pending_approval",
            approval_needed=ApprovalNeeded(
                tool_name=exc.tool_name,
                tool_input=exc.tool_input,
            ),
            cost_eur=summary["total_cost_eur"],
            steps_taken=summary["total_steps"],
        )

    except (CostLimitReached, StepLimitReached) as exc:
        session.mark_error(str(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    except Exception as exc:  # noqa: BLE001
        logger.error(f"Agent run error for session {session.session_id}: {exc}")
        session.mark_error(str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent error: {exc}",
        )


@router.post(
    "/approve/{session_id}",
    response_model=ApproveResponse,
    summary="Approve or deny a pending tool call",
    description=(
        "When a task is in 'pending_approval' state, call this endpoint "
        "to approve or deny the YELLOW zone tool call.  "
        "If approved, the tool is executed and the agent continues.  "
        "If denied, the session is marked as 'denied' and no action is taken."
    ),
)
def approve_tool_call(
    session_id: str,
    request: ApproveRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> ApproveResponse:
    """Approve or deny a pending YELLOW zone tool call and resume the agent.

    Args:
        session_id:   UUID of the session awaiting approval.
        request:      Whether to approve and an optional reason.
        current_user: JWT payload injected by auth dependency.

    Returns:
        ApproveResponse with updated status and result.

    Raises:
        HTTPException 404: Session not found.
        HTTPException 409: Session is not in 'pending_approval' state.
    """
    session = get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    if session.status != "pending_approval":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Session is in '{session.status}' state, "
                "not 'pending_approval'."
            ),
        )

    # --- Human denied ---
    if not request.approved:
        session.mark_denied()
        return ApproveResponse(
            session_id=session_id,
            status="denied",
            result=session.result,
            cost_eur=session.cost_eur,
            steps_taken=session.steps_taken,
        )

    # --- Human approved: run the tool directly, then resume the agent ---
    try:
        agent = _build_agent_for("email")  # only email agent for now
        tool = agent._tools.get(session.approval_tool_name)
        if tool is None:
            raise ValueError(
                f"Tool '{session.approval_tool_name}' not found on agent."
            )

        # Execute the approved tool
        tool_result = tool.run(session.approval_tool_input)

        # Reconstruct the full message history:
        # saved snapshot + assistant msg that triggered the approval + tool result
        resume_messages = list(session.approval_messages) + [
            {
                "role": "assistant",
                "content": session.approval_assistant_content,
                "tool_call": session.approval_tool_call,
            },
            {
                "role": "tool",
                "name": session.approval_tool_name,
                "content": tool_result,
            },
        ]

        # Resume the agent from the injected state
        result = agent.run(session.task, initial_messages=resume_messages)
        cost_summary = agent.get_cost_summary()

        # Merge audit logs: pre-approval entries + resume entries
        merged_audit = session.audit_log + agent.get_audit_log()
        total_cost = round(session.cost_eur + cost_summary["total_cost_eur"], 6)
        total_steps = session.steps_taken + cost_summary["total_steps"]

        session.mark_completed(
            result=result,
            audit_log=merged_audit,
            cost_eur=total_cost,
            steps_taken=total_steps,
        )
        return ApproveResponse(
            session_id=session_id,
            status="completed",
            result=result,
            cost_eur=total_cost,
            steps_taken=total_steps,
        )

    except ApprovalRequired as exc:
        # Another YELLOW tool encountered after resuming
        pa = agent.pending_approval or {}
        session.mark_pending_approval(
            tool_name=exc.tool_name,
            tool_input=exc.tool_input,
            messages_snapshot=pa.get("messages_snapshot", []),
            assistant_content=pa.get("last_assistant_content", ""),
            tool_call=pa.get("last_tool_call", {}),
            audit_log=session.audit_log + agent.get_audit_log(),
            cost_eur=round(session.cost_eur + agent.get_cost_summary()["total_cost_eur"], 6),
            steps_taken=session.steps_taken + agent.get_cost_summary()["total_steps"],
        )
        return ApproveResponse(
            session_id=session_id,
            status="pending_approval",
            approval_needed=ApprovalNeeded(
                tool_name=exc.tool_name,
                tool_input=exc.tool_input,
            ),
            cost_eur=session.cost_eur,
            steps_taken=session.steps_taken,
        )

    except Exception as exc:  # noqa: BLE001
        logger.error(f"Agent resume error for session {session_id}: {exc}")
        session.mark_error(str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent resume error: {exc}",
        )


@router.get(
    "/sessions",
    response_model=list[SessionSummary],
    summary="List all agent sessions",
)
def get_sessions(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> list[SessionSummary]:
    """Return a summary list of all sessions, newest first."""
    return [
        SessionSummary(
            session_id=s.session_id,
            status=s.status,
            task=s.task,
            created_at=s.created_at,
            cost_eur=s.cost_usd,
            steps_taken=s.steps_taken,
        )
        for s in list_sessions()
    ]


@router.get(
    "/sessions/{session_id}",
    response_model=SessionSummary,
    summary="Get a single session summary",
)
def get_session_summary(
    session_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> SessionSummary:
    """Return the summary for a single session.

    Raises:
        HTTPException 404: If the session does not exist.
    """
    session = get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    return SessionSummary(
        session_id=session.session_id,
        status=session.status,
        task=session.task,
        created_at=session.created_at,
        cost_eur=session.cost_eur,
        steps_taken=session.steps_taken,
    )
