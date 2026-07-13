"""
Audit log routes.

GET /audit-log/{session_id} — retrieve the full audit trail for a session.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import get_current_user
from api.models import AuditEntry, AuditLogResponse
from api.session import get_session

router = APIRouter(prefix="/audit-log", tags=["Audit Log"])


@router.get(
    "/{session_id}",
    response_model=AuditLogResponse,
    summary="Get audit log for a session",
    description=(
        "Returns the complete audit trail for the given session, including "
        "every LLM call, tool invocation, tool result, and any errors or "
        "limit events.  Useful for the frontend to display a step-by-step "
        "activity feed."
    ),
)
def get_audit_log(
    session_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> AuditLogResponse:
    """Return the full audit log for a session.

    Args:
        session_id:   UUID of the session.
        current_user: JWT payload injected by auth dependency.

    Returns:
        AuditLogResponse with all entries and cost totals.

    Raises:
        HTTPException 404: If the session does not exist.
    """
    session = get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    entries = [AuditEntry(**e) for e in session.audit_log]

    return AuditLogResponse(
        session_id=session_id,
        entries=entries,
        total_entries=len(entries),
        total_cost_eur=session.cost_eur,
    )
