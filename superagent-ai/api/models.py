"""
Pydantic request and response models for the Super Agent API.

All inputs and outputs are strongly typed here so FastAPI can
auto-generate accurate OpenAPI / Swagger documentation.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Auth models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """Credentials for obtaining a JWT token pair."""

    username: str = Field(..., example="admin")
    password: str = Field(..., example="changeme")


class TokenResponse(BaseModel):
    """JWT token pair returned after successful login or refresh."""

    access_token: str = Field(..., description="Short-lived JWT access token")
    refresh_token: str = Field(..., description="Long-lived JWT refresh token")
    token_type: str = Field(default="bearer")
    expires_in: int = Field(..., description="Access token lifetime in seconds")


class RefreshRequest(BaseModel):
    """Refresh token used to obtain a new access token."""

    refresh_token: str


# ---------------------------------------------------------------------------
# Agent / task models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    """Request body for POST /agent/run."""

    task: str = Field(
        ...,
        example="Check my inbox and summarise any invoice emails.",
        description="Natural-language task for the EmailAgent to perform.",
    )
    agent: Literal["email"] = Field(
        default="email",
        description="Which agent to use.  Currently only 'email' is supported.",
    )


class ApprovalNeeded(BaseModel):
    """Details of the YELLOW zone tool call awaiting human approval."""

    tool_name: str = Field(..., example="send_email")
    tool_input: str = Field(
        ...,
        example='{"to": "vendor@example.com", "subject": "Re: Invoice", "body": "..."}',
        description="Raw JSON input the agent wants to pass to the tool.",
    )


class RunResponse(BaseModel):
    """Response body for POST /agent/run."""

    session_id: str = Field(..., description="Unique session identifier.")
    status: Literal["completed", "pending_approval", "error"] = Field(
        ...,
        description=(
            "'completed' — agent finished and returned a result.\n"
            "'pending_approval' — agent hit a YELLOW zone tool and is waiting.\n"
            "'error' — an unexpected error occurred."
        ),
    )
    result: str | None = Field(
        default=None,
        description="Final agent output.  Present when status='completed'.",
    )
    approval_needed: ApprovalNeeded | None = Field(
        default=None,
        description="Tool call details for the human to review.  Present when status='pending_approval'.",
    )
    cost_usd: float = Field(default=0.0, description="Estimated LLM cost for this run in USD.")
    steps_taken: int = Field(default=0)


class ApproveRequest(BaseModel):
    """Request body for POST /agent/approve/{session_id}."""

    approved: bool = Field(
        ...,
        description="True to approve and execute the tool call; False to deny it.",
    )
    reason: str | None = Field(
        default=None,
        example="Draft looks good — send it.",
        description="Optional human note recorded in the audit log.",
    )


class ApproveResponse(BaseModel):
    """Response body for POST /agent/approve/{session_id}."""

    session_id: str
    status: Literal["completed", "pending_approval", "denied", "error"]
    result: str | None = None
    approval_needed: ApprovalNeeded | None = None
    cost_usd: float = 0.0
    steps_taken: int = 0


# ---------------------------------------------------------------------------
# Email models
# ---------------------------------------------------------------------------


class EmailItem(BaseModel):
    """A single email fetched from Gmail."""

    id: str
    subject: str
    sender: str
    date: str
    body_preview: str
    full_body: str
    has_attachments: bool


class EmailsResponse(BaseModel):
    """Response body for GET /emails."""

    emails: list[EmailItem]
    count: int
    error: str | None = None


# ---------------------------------------------------------------------------
# Audit log models
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """A single audit log entry."""

    timestamp: str
    event_type: str
    details: dict[str, Any]
    step_number: int
    cost_so_far: float


class AuditLogResponse(BaseModel):
    """Response body for GET /audit-log/{session_id}."""

    session_id: str
    entries: list[AuditEntry]
    total_entries: int
    total_cost_usd: float


# ---------------------------------------------------------------------------
# Session list model
# ---------------------------------------------------------------------------


class SessionSummary(BaseModel):
    """Summary of a single agent session."""

    session_id: str
    status: str
    task: str
    created_at: str
    cost_usd: float
    steps_taken: int
