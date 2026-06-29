"""
result_handler.py — Django↔AI bridge: result formatting.

Converts raw TaskResult / ApprovalResult into a dict the Django
views and WebSocket consumers can serialise directly to JSON.

Usage:
    from api.result_handler import ResultHandler
    from api.task_handler import TaskResult

    payload = ResultHandler.format_task_result(result, task_id="abc")
    # → serialisable dict ready for DRF Response / WebSocket send
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ResultHandler:
    """Formats bridge results for Django consumption."""

    # ------------------------------------------------------------------
    # Task results
    # ------------------------------------------------------------------

    @staticmethod
    def format_task_result(result, task_id: str | None = None) -> dict[str, Any]:
        """Format a TaskResult for Django.

        Args:
            result:  TaskResult dataclass from task_handler.
            task_id: Override task_id (defaults to result.task_id).

        Returns:
            Dict with keys: task_id, status, result, error,
            steps_taken, cost_usd, has_approval, approval_summary,
            audit_log_count, formatted_at.
        """
        tid = task_id or result.task_id
        has_approval = result.approval_payload is not None

        approval_summary: dict[str, Any] | None = None
        if has_approval and result.approval_payload:
            approval_summary = {
                "tool_name": result.approval_payload.get("tool_name", ""),
                "tool_input": result.approval_payload.get("tool_input", ""),
            }

        return {
            "task_id": tid,
            "status": result.status,
            "result": result.result,
            "error": result.error,
            "steps_taken": result.steps_taken,
            "cost_usd": round(result.cost_usd, 6),
            "has_approval": has_approval,
            "approval_summary": approval_summary,
            "audit_log_count": len(result.audit_log),
            "formatted_at": _now_iso(),
        }

    @staticmethod
    def format_approval_result(result, task_id: str | None = None) -> dict[str, Any]:
        """Format an ApprovalResult for Django.

        Returns:
            Dict with keys: task_id, status, result, error,
            steps_taken, cost_usd, has_approval, approval_summary,
            audit_log_count, formatted_at.
        """
        tid = task_id or result.task_id
        has_approval = result.approval_payload is not None

        approval_summary: dict[str, Any] | None = None
        if has_approval and result.approval_payload:
            approval_summary = {
                "tool_name": result.approval_payload.get("tool_name", ""),
                "tool_input": result.approval_payload.get("tool_input", ""),
            }

        return {
            "task_id": tid,
            "status": result.status,
            "result": result.result,
            "error": result.error,
            "steps_taken": result.steps_taken,
            "cost_usd": round(result.cost_usd, 6),
            "has_approval": has_approval,
            "approval_summary": approval_summary,
            "audit_log_count": len(result.audit_log),
            "formatted_at": _now_iso(),
        }

    # ------------------------------------------------------------------
    # WebSocket event payloads
    # ------------------------------------------------------------------

    @staticmethod
    def ws_task_started(task_id: str) -> dict[str, Any]:
        return {"event": "task_started", "task_id": task_id, "timestamp": _now_iso()}

    @staticmethod
    def ws_task_completed(task_id: str, result: str, steps: int, cost: float) -> dict[str, Any]:
        return {
            "event": "task_completed",
            "task_id": task_id,
            "result": result,
            "steps_taken": steps,
            "cost_usd": round(cost, 6),
            "timestamp": _now_iso(),
        }

    @staticmethod
    def ws_approval_required(
        task_id: str,
        approval_id: str,
        tool_name: str,
        tool_input: Any,
    ) -> dict[str, Any]:
        return {
            "event": "approval_required",
            "task_id": task_id,
            "approval_id": approval_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "timestamp": _now_iso(),
        }

    @staticmethod
    def ws_task_failed(task_id: str, error: str) -> dict[str, Any]:
        return {
            "event": "task_failed",
            "task_id": task_id,
            "error": error,
            "timestamp": _now_iso(),
        }

    @staticmethod
    def ws_task_resumed(task_id: str) -> dict[str, Any]:
        return {"event": "task_resumed", "task_id": task_id, "timestamp": _now_iso()}

    @staticmethod
    def ws_task_cancelled(task_id: str, reason: str = "") -> dict[str, Any]:
        return {
            "event": "task_cancelled",
            "task_id": task_id,
            "reason": reason,
            "timestamp": _now_iso(),
        }

    # ------------------------------------------------------------------
    # Audit log formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_audit_log(audit_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalise audit log entries for Django serialisation.

        Ensures every entry has: timestamp, event_type, step, details.
        """
        out = []
        for entry in audit_log:
            out.append({
                "timestamp": entry.get("timestamp", _now_iso()),
                "event_type": entry.get("event_type", "unknown"),
                "step": entry.get("step_number", entry.get("details", {}).get("step", 0)),
                "details": entry.get("details", {}),
            })
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
