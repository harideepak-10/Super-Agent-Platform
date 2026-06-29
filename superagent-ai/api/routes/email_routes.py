"""
Email routes.

GET /emails — fetch emails directly from Gmail without running the full agent.
              Useful for the frontend to populate an inbox view.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.auth import get_current_user
from api.models import EmailItem, EmailsResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/emails", tags=["Emails"])


@router.get(
    "",
    response_model=EmailsResponse,
    summary="Fetch emails from Gmail",
    description=(
        "Directly fetches emails from Gmail using the ReadEmailsTool.  "
        "Does not run the full agent — just reads the inbox.  "
        "Requires GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, and "
        "GMAIL_REFRESH_TOKEN environment variables to be set."
    ),
)
def get_emails(
    limit: int = Query(default=10, ge=1, le=50, description="Max number of emails to fetch"),
    filter: str = Query(  # noqa: A002
        default="is:unread",
        description="Gmail search query (e.g. 'is:unread', 'from:vendor@example.com')",
    ),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> EmailsResponse:
    """Fetch emails from Gmail.

    Args:
        limit:        Maximum number of emails to return (1–50).
        filter:       Gmail search query string.
        current_user: JWT payload injected by auth dependency.

    Returns:
        EmailsResponse with a list of email objects.

    Raises:
        HTTPException 503: If Gmail credentials are not configured.
        HTTPException 502: If the Gmail API call fails.
    """
    try:
        from core.tools.gmail.read_emails import ReadEmailsTool
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Gmail tools not available: {exc}",
        )

    tool = ReadEmailsTool()  # uses real GmailAuth (no injected service)

    try:
        raw = tool.run(json.dumps({"limit": limit, "filter": filter}))
        parsed = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Gmail read error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch emails from Gmail: {exc}",
        )

    # Handle graceful error response from the tool
    if isinstance(parsed, dict) and "error" in parsed:
        return EmailsResponse(
            emails=[],
            count=0,
            error=parsed["error"],
        )

    emails = [EmailItem(**e) for e in parsed]
    return EmailsResponse(emails=emails, count=len(emails))
