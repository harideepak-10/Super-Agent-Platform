"""
Flag invoice tool — marks an invoice for human review.

Zone: YELLOW — ALWAYS requires human approval before execution.

Flagging changes the invoice's status in the data store and creates
an audit trail.  Because flagging has real downstream consequences
(payment held, vendor notified), BaseAgent raises ApprovalRequired
before this tool's run() is ever called automatically.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_VALID_REASONS = {
    "duplicate",
    "amount_mismatch",
    "missing_information",
    "suspicious_vendor",
    "overdue",
    "over_budget",
    "manual_review",
}


class FlagInvoiceTool(BaseTool):
    """Flag an invoice for human review in the data store.

    Zone: YELLOW — BaseAgent raises ApprovalRequired before
    this tool's ``run()`` is ever called automatically.

    Input format (JSON string)::

        {
            "invoice_id":   "INV-001",
            "reason":       "duplicate",       // see _VALID_REASONS
            "notes":        "Same number as INV-002 from same vendor",
            "flagged_by":   "FinanceAgent"     // optional
        }

    Returns:
        JSON dict with:
            ``status``     : "flagged"
            ``invoice_id`` : str
            ``reason``     : str
            ``notes``      : str
            ``timestamp``  : ISO 8601 UTC string
    """

    name: str = "flag_invoice"
    description: str = (
        "Flags an invoice for human review. YELLOW zone — always requires approval. "
        "Input JSON: {\"invoice_id\": \"INV-001\", \"reason\": \"duplicate\", "
        "\"notes\": \"...\"}. "
        f"Valid reasons: {', '.join(sorted(_VALID_REASONS))}. "
        "Returns JSON with status, invoice_id, reason, notes, timestamp."
    )
    zone: ToolZone = ToolZone.YELLOW  # ← Never changes

    def __init__(self, invoice_store: Any = None) -> None:
        self._store = invoice_store

    def run(self, input_str: str) -> str:
        """Flag the invoice (only called after human approval).

        Args:
            input_str: JSON string with invoice_id, reason, notes.

        Returns:
            JSON result string.

        Raises:
            ValueError: If required fields are missing or reason is invalid.
            RuntimeError: If the store update fails.
        """
        params = self._parse_and_validate(input_str)
        invoice_id: str = params["invoice_id"]
        reason: str = params["reason"]
        notes: str = params.get("notes", "")
        flagged_by: str = params.get("flagged_by", "FinanceAgent")

        logger.info(f"FlagInvoiceTool: flagging {invoice_id!r} — reason: {reason!r}")

        if self._store is not None:
            try:
                self._store.flag(invoice_id, reason=reason, notes=notes)
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to flag invoice {invoice_id!r} in store: {exc}"
                ) from exc

        timestamp = datetime.now(timezone.utc).isoformat()
        result = {
            "status": "flagged",
            "invoice_id": invoice_id,
            "reason": reason,
            "notes": notes,
            "flagged_by": flagged_by,
            "timestamp": timestamp,
        }
        logger.info(f"FlagInvoiceTool: {invoice_id!r} flagged at {timestamp}")
        return json.dumps(result)

    @staticmethod
    def _parse_and_validate(input_str: str) -> dict[str, str]:
        if not input_str or not input_str.strip():
            raise ValueError("FlagInvoiceTool received empty input.")
        try:
            params = json.loads(input_str)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"FlagInvoiceTool expects JSON with 'invoice_id' and 'reason'. "
                f"Got: {input_str!r}"
            ) from exc

        if not params.get("invoice_id"):
            raise ValueError("FlagInvoiceTool: 'invoice_id' is required.")
        if not params.get("reason"):
            raise ValueError("FlagInvoiceTool: 'reason' is required.")
        if params["reason"] not in _VALID_REASONS:
            raise ValueError(
                f"FlagInvoiceTool: invalid reason '{params['reason']}'. "
                f"Valid: {sorted(_VALID_REASONS)}"
            )
        return {k: str(v) for k, v in params.items()}
