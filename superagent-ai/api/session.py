"""
In-memory session store for the Super Agent API.

Each call to POST /agent/run creates a session.  Sessions persist in a
module-level dict for the lifetime of the server process.  For
production, replace this with Redis or a database-backed store.

Session lifecycle:
    running          → agent is executing
    completed        → agent finished successfully
    pending_approval → agent hit a YELLOW zone tool, waiting for human
    denied           → human denied the tool call
    error            → unhandled exception during agent run
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal


SessionStatus = Literal[
    "running", "completed", "pending_approval", "denied", "error"
]


class Session:
    """Represents a single agent task session.

    Attributes:
        session_id:   Unique identifier.
        task:         Original task string.
        status:       Current lifecycle status.
        result:       Final agent result (set on completion).
        error:        Error message (set on failure).
        audit_log:    Accumulated audit entries.
        cost_eur:     Cumulative LLM cost.
        steps_taken:  Number of agent loop steps completed.
        created_at:   ISO 8601 UTC creation timestamp.

        # Fields populated when status == "pending_approval"
        approval_tool_name:  Name of the YELLOW zone tool awaiting approval.
        approval_tool_input: Input string the agent wants to pass.
        approval_messages:   Full message history snapshot for resume.
        approval_assistant_content: Last assistant content before approval.
        approval_tool_call:  The raw tool_call dict from the LLM response.
    """

    def __init__(self, task: str) -> None:
        self.session_id: str = str(uuid.uuid4())
        self.task: str = task
        self.status: SessionStatus = "running"
        self.result: str | None = None
        self.error: str | None = None
        self.audit_log: list[dict[str, Any]] = []
        self.cost_eur: float = 0.0
        self.steps_taken: int = 0
        self.created_at: str = datetime.now(timezone.utc).isoformat()

        # Approval-resume fields
        self.approval_tool_name: str = ""
        self.approval_tool_input: str = ""
        self.approval_messages: list[dict[str, Any]] = []
        self.approval_assistant_content: str = ""
        self.approval_tool_call: dict[str, Any] = {}

    def mark_completed(
        self,
        result: str,
        audit_log: list[dict[str, Any]],
        cost_eur: float,
        steps_taken: int,
    ) -> None:
        """Transition the session to 'completed' state."""
        self.status = "completed"
        self.result = result
        self.audit_log = audit_log
        self.cost_eur = cost_eur
        self.steps_taken = steps_taken

    def mark_pending_approval(
        self,
        tool_name: str,
        tool_input: str,
        messages_snapshot: list[dict[str, Any]],
        assistant_content: str,
        tool_call: dict[str, Any],
        audit_log: list[dict[str, Any]],
        cost_eur: float,
        steps_taken: int,
    ) -> None:
        """Transition the session to 'pending_approval' state."""
        self.status = "pending_approval"
        self.approval_tool_name = tool_name
        self.approval_tool_input = tool_input
        self.approval_messages = messages_snapshot
        self.approval_assistant_content = assistant_content
        self.approval_tool_call = tool_call
        self.audit_log = audit_log
        self.cost_eur = cost_eur
        self.steps_taken = steps_taken

    def mark_denied(self) -> None:
        """Transition the session to 'denied' state."""
        self.status = "denied"
        self.result = (
            f"Tool '{self.approval_tool_name}' was denied by the operator."
        )

    def mark_error(self, error_msg: str) -> None:
        """Transition the session to 'error' state."""
        self.status = "error"
        self.error = error_msg


# ---------------------------------------------------------------------------
# Module-level store
# ---------------------------------------------------------------------------

_store: dict[str, Session] = {}


def create_session(task: str) -> Session:
    """Create a new session and add it to the store.

    Args:
        task: The task string for this session.

    Returns:
        The newly created Session object.
    """
    session = Session(task)
    _store[session.session_id] = session
    return session


def get_session(session_id: str) -> Session | None:
    """Retrieve a session by ID.

    Args:
        session_id: UUID string for the session.

    Returns:
        The Session object, or None if not found.
    """
    return _store.get(session_id)


def list_sessions() -> list[Session]:
    """Return all sessions sorted by creation time (newest first)."""
    return sorted(_store.values(), key=lambda s: s.created_at, reverse=True)


def delete_session(session_id: str) -> bool:
    """Remove a session from the store.

    Args:
        session_id: UUID string for the session.

    Returns:
        True if the session existed and was removed.
    """
    if session_id in _store:
        del _store[session_id]
        return True
    return False
