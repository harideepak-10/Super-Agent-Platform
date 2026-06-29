"""
Flag issue tool — QA Agent flags a problem for human review.

Zone: GREEN — QA flags are informational; they do not mutate data.

Unlike flag_invoice (YELLOW), this tool only records the issue in
the audit trail.  No data store is modified.  A human reviews QA
flags and decides whether to escalate.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_SEVERITY_LEVELS = {"low", "medium", "high", "critical"}

_ISSUE_TYPES = {
    "number_mismatch",
    "missing_section",
    "placeholder_text",
    "logic_error",
    "incomplete_output",
    "policy_violation",
    "format_error",
    "other",
}


class FlagIssueTool(BaseTool):
    """Record a QA issue found in agent output.

    Zone: GREEN — recording an issue is non-destructive and does not
    require human approval.  The human reviews the QA report separately.

    Input format (JSON string)::

        {
            "source_agent": "FinanceAgent",
            "issue_type":   "number_mismatch",
            "severity":     "high",
            "description":  "Invoice INV-001 total $500 does not match line items $480.",
            "context":      "Section: Invoice Summary"   // optional
        }

    Returns:
        JSON dict with:
            ``status``      : "flagged"
            ``issue_id``    : str (timestamp-based)
            ``source_agent``: str
            ``issue_type``  : str
            ``severity``    : str
            ``description`` : str
            ``timestamp``   : ISO 8601 UTC
    """

    name: str = "flag_issue"
    description: str = (
        "Records a QA issue found in agent output. GREEN zone — informational only. "
        "Input JSON: {\"source_agent\": \"...\", \"issue_type\": \"number_mismatch\", "
        "\"severity\": \"high\", \"description\": \"...\"}. "
        f"Issue types: {', '.join(sorted(_ISSUE_TYPES))}. "
        f"Severity: {', '.join(sorted(_SEVERITY_LEVELS))}. "
        "Returns JSON with status, issue_id, severity, description, timestamp."
    )
    zone: ToolZone = ToolZone.GREEN  # QA flags are informational, not destructive

    def __init__(self, issue_log: list[dict[str, Any]] | None = None) -> None:
        """Initialise with an optional shared issue log list.

        Args:
            issue_log: If provided, flagged issues are appended here.
                       Useful for tests to inspect what was flagged.
        """
        self._issue_log = issue_log

    def run(self, input_str: str) -> str:
        try:
            params = self._parse_and_validate(input_str)
        except ValueError as exc:
            return json.dumps({"error": str(exc), "status": "error"})

        timestamp = datetime.now(timezone.utc).isoformat()
        issue_id = f"QA-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

        result = {
            "status": "flagged",
            "issue_id": issue_id,
            "source_agent": params.get("source_agent", "unknown"),
            "issue_type": params["issue_type"],
            "severity": params["severity"],
            "description": params["description"],
            "context": params.get("context", ""),
            "timestamp": timestamp,
        }

        if self._issue_log is not None:
            self._issue_log.append(result)

        logger.info(
            f"FlagIssueTool: [{params['severity'].upper()}] {params['issue_type']} "
            f"from {params.get('source_agent', '?')} — {params['description'][:80]}"
        )

        return json.dumps(result)

    @staticmethod
    def _parse_and_validate(input_str: str) -> dict[str, Any]:
        if not input_str or not input_str.strip():
            raise ValueError("FlagIssueTool received empty input.")
        try:
            params = json.loads(input_str)
        except json.JSONDecodeError as exc:
            raise ValueError(f"FlagIssueTool expects JSON. Got: {input_str!r}") from exc

        if not params.get("issue_type"):
            raise ValueError("FlagIssueTool: 'issue_type' is required.")
        if params["issue_type"] not in _ISSUE_TYPES:
            raise ValueError(
                f"Invalid issue_type '{params['issue_type']}'. "
                f"Valid: {sorted(_ISSUE_TYPES)}"
            )
        if not params.get("severity"):
            params["severity"] = "medium"
        if params["severity"] not in _SEVERITY_LEVELS:
            raise ValueError(
                f"Invalid severity '{params['severity']}'. "
                f"Valid: {sorted(_SEVERITY_LEVELS)}"
            )
        if not params.get("description"):
            raise ValueError("FlagIssueTool: 'description' is required.")
        return params
