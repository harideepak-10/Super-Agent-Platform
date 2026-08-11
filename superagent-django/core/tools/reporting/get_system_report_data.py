"""
GetSystemReportDataTool — real, aggregated data about the SuperAgent system's
own operation (tasks, agent activity, tool usage, costs, deliverables),
scoped to the current workspace, for the Reporting Agent to build reports from.

Zone: GREEN — read-only, no approval needed.

Design principle: this tool is the ONLY source of numbers for any report.
Every count/sum/list it returns is a real database query result. If a field
has no data, it comes back as 0 or an empty list — the caller (Reporting
Agent) must report that honestly, never substitute a guess.
"""
from __future__ import annotations

import json

from core.tools.base_tool import BaseTool, ToolZone


_EMAIL_TOOLS = {
    "send_email", "read_email", "search_emails", "reply_to_email", "forward_email",
    "schedule_email", "create_draft", "create_gmail_draft", "delete_email",
    "mark_as_read", "label_email", "move_to_folder", "download_attachment",
    "read_email_attachment_content", "summarize_emails",
}
_MEETING_TOOLS = {
    "list_events", "get_event", "find_free_slots", "set_reminder",
    "check_attendee_availability", "detect_conflicts", "suggest_meeting_time",
    "create_meeting", "create_recurring_event", "update_event", "delete_event",
    "respond_to_invite", "block_focus_time", "send_meeting_summary",
}
_DOCUMENT_TOOLS = {
    "read_from_drive", "summarize_document", "extract_tables", "ocr_document",
    "generate_content", "create_pdf", "create_docx", "create_presentation",
    "fill_template", "merge_pdfs", "compare_documents", "translate_document",
    "upload_to_drive", "export_csv",
}


class GetSystemReportDataTool(BaseTool):
    name: str = "get_system_report_data"
    description: str = (
        "Get REAL aggregated data about the SuperAgent system's own operation, scoped to "
        "the current workspace. NEVER invent report numbers — always call this tool first "
        "and use exactly what it returns; a zero or empty result is a real, honest answer. "
        "Input JSON: {\"report_type\": \"task_activity|agent_activity|email_activity|"
        "meeting_activity|document_activity|productivity|execution_history|success_failure|"
        "tool_usage|cost_usage|combined\", "
        "\"period\": \"today|yesterday|this_week|last_week|this_month|last_month|custom\", "
        "\"date_from\": \"YYYY-MM-DD (only with period=custom)\", "
        "\"date_to\": \"YYYY-MM-DD (only with period=custom)\", "
        "\"agent_name\": \"...(optional filter, e.g. 'Calendar Agent')\", "
        "\"limit\": 50 (optional, max rows for list-style reports, capped at 200)}. "
        "Use report_type='combined' for a daily/weekly/monthly EOD or business report that "
        "needs everything at once."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    # ------------------------------------------------------------------
    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else (input_str or {})
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input. Expected JSON."})

        if not isinstance(data, dict):
            data = {}

        report_type = str(data.get("report_type") or "combined").lower().strip()
        period = str(data.get("period") or "today").lower().strip()
        agent_name_filter = str(data.get("agent_name") or "").strip()
        try:
            limit = min(int(data.get("limit", 50) or 50), 200)
        except (TypeError, ValueError):
            limit = 50

        if not self._workspace_id:
            return json.dumps({
                "error": "No workspace context available — cannot safely scope report data to a workspace."
            })

        try:
            from apps.tasks.models import Task, TaskStep
        except Exception as exc:
            return json.dumps({
                "error": (
                    f"Could not import Task/TaskStep models from apps.tasks.models: {exc}. "
                    "The reporting tool's field names may not match this deployment's actual "
                    "model definitions — check apps/tasks/models.py."
                )
            })

        date_from, date_to, label = self._resolve_period(period, data.get("date_from"), data.get("date_to"))
        if date_from is None:
            return json.dumps({
                "error": (
                    "Invalid period. Use one of: today, yesterday, this_week, last_week, "
                    "this_month, last_month, or period='custom' with date_from/date_to as YYYY-MM-DD."
                )
            })

        try:
            qs = Task.objects.filter(
                workspace_id=self._workspace_id,
                created_at__gte=date_from,
                created_at__lt=date_to,
            )
            if agent_name_filter:
                qs = qs.filter(agent__name__icontains=agent_name_filter)
        except Exception as exc:
            return json.dumps({"error": f"Query failed — check Task model field names: {exc}"})

        handlers = {
            "task_activity":     self._task_activity,
            "agent_activity":    self._agent_activity,
            "email_activity":    self._domain_activity(_EMAIL_TOOLS, "emails_sent", "send_email"),
            "meeting_activity":  self._domain_activity(_MEETING_TOOLS, "meetings_created", "create_meeting"),
            "document_activity": self._domain_activity(_DOCUMENT_TOOLS, "files_created", None),
            "productivity":      self._productivity,
            "execution_history": self._execution_history,
            "success_failure":   self._success_failure,
            "tool_usage":        self._tool_usage,
            "cost_usage":        self._cost_usage,
        }

        meta = {
            "period": label,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "agent_filter": agent_name_filter or None,
            "total_tasks_in_period": qs.count(),
        }

        if report_type in ("combined", "eod"):
            result = {"report_type": report_type, **meta}
            for key, fn in handlers.items():
                try:
                    result[key] = fn(qs, TaskStep, limit)
                except Exception as exc:
                    result[key] = {"error": str(exc)}
            return json.dumps(result, default=str, ensure_ascii=False)

        fn = handlers.get(report_type)
        if not fn:
            return json.dumps({
                "error": f"Unknown report_type '{report_type}'. Valid: {', '.join(handlers)}, combined."
            })

        try:
            payload = fn(qs, TaskStep, limit)
        except Exception as exc:
            return json.dumps({"error": f"Report query failed: {exc}"})

        return json.dumps({"report_type": report_type, **meta, "data": payload}, default=str, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Period resolution
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_period(period, date_from_raw, date_to_raw):
        from datetime import datetime, timedelta, time
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Kolkata")
        now = datetime.now(tz)
        today_start = datetime.combine(now.date(), time.min, tzinfo=tz)

        if period == "today":
            return today_start, today_start + timedelta(days=1), "today"
        if period == "yesterday":
            start = today_start - timedelta(days=1)
            return start, today_start, "yesterday"
        if period == "this_week":
            start = today_start - timedelta(days=now.weekday())
            return start, start + timedelta(days=7), "this_week"
        if period == "last_week":
            this_week_start = today_start - timedelta(days=now.weekday())
            start = this_week_start - timedelta(days=7)
            return start, this_week_start, "last_week"
        if period == "this_month":
            start = today_start.replace(day=1)
            end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
            return start, end, "this_month"
        if period == "last_month":
            this_month_start = today_start.replace(day=1)
            if this_month_start.month == 1:
                start = this_month_start.replace(year=this_month_start.year - 1, month=12)
            else:
                start = this_month_start.replace(month=this_month_start.month - 1)
            return start, this_month_start, "last_month"
        if period == "custom":
            try:
                start = datetime.strptime(str(date_from_raw), "%Y-%m-%d").replace(tzinfo=tz)
                end = datetime.strptime(str(date_to_raw), "%Y-%m-%d").replace(tzinfo=tz) + timedelta(days=1)
                return start, end, f"{date_from_raw} to {date_to_raw}"
            except Exception:
                return None, None, None
        return None, None, None

    # ------------------------------------------------------------------
    # Report builders — each operates on an already workspace+date scoped Task queryset
    # ------------------------------------------------------------------
    @staticmethod
    def _task_activity(qs, TaskStep, limit):
        from django.db.models import Count
        total = qs.count()
        by_status = {row["status"]: row["n"] for row in qs.values("status").annotate(n=Count("id"))}
        by_priority = {row["priority"]: row["n"] for row in qs.values("priority").annotate(n=Count("id"))} \
            if hasattr(qs.model, "priority") else {}
        return {"total_tasks": total, "by_status": by_status, "by_priority": by_priority}

    @staticmethod
    def _agent_activity(qs, TaskStep, limit):
        from django.db.models import Count
        rows = (
            qs.exclude(agent__isnull=True)
            .values("agent__name")
            .annotate(total=Count("id"))
            .order_by("-total")[:limit]
        )
        return [{"agent": r["agent__name"], "tasks": r["total"]} for r in rows]

    @staticmethod
    def _domain_activity(tool_set, primary_metric_name, primary_tool_name):
        """Returns a bound function summarizing TaskStep activity for a set of tool names."""
        def _fn(qs, TaskStep, limit):
            from django.db.models import Count
            task_ids = list(qs.values_list("id", flat=True))
            calls = TaskStep.objects.filter(
                task_id__in=task_ids,
                tool_name__in=tool_set,
                step_type=TaskStep.StepType.TOOL_CALL,
            )
            by_tool = {
                r["tool_name"]: r["n"]
                for r in calls.values("tool_name").annotate(n=Count("id")).order_by("-n")
            }
            primary_count = by_tool.get(primary_tool_name, 0) if primary_tool_name else sum(by_tool.values())
            result = {
                "total_calls": calls.count(),
                "calls_by_tool": by_tool,
            }
            if primary_tool_name:
                result[primary_metric_name] = primary_count
            return result
        return _fn

    @staticmethod
    def _productivity(qs, TaskStep, limit):
        completed = qs.filter(status="completed").count()
        failed = qs.filter(status="failed").count()
        cancelled = qs.filter(status="cancelled").count()
        waiting = qs.filter(status="waiting_approval").count()
        total = qs.count()
        steps_vals = list(qs.exclude(steps_taken__isnull=True).values_list("steps_taken", flat=True))
        avg_steps = round(sum(steps_vals) / len(steps_vals), 1) if steps_vals else None
        return {
            "total_tasks": total,
            "completed": completed,
            "failed": failed,
            "cancelled": cancelled,
            "waiting_approval": waiting,
            "completion_rate_pct": round((completed / total * 100), 1) if total else 0,
            "avg_steps_per_task": avg_steps,
        }

    @staticmethod
    def _execution_history(qs, TaskStep, limit):
        rows = qs.order_by("-created_at")[:limit]
        out = []
        for t in rows:
            out.append({
                "id": str(t.id),
                "prompt": (t.prompt or "")[:200],
                "status": t.status,
                "agent": t.agent.name if getattr(t, "agent_id", None) else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "steps_taken": t.steps_taken,
                "cost_usd": float(t.cost_usd or 0),
                "error_message": t.error_message or None,
            })
        return out

    @staticmethod
    def _success_failure(qs, TaskStep, limit):
        from django.db.models import Count
        rows = qs.exclude(agent__isnull=True).values("agent__name", "status").annotate(n=Count("id"))
        out: dict = {}
        for r in rows:
            out.setdefault(r["agent__name"], {})[r["status"]] = r["n"]
        return out

    @staticmethod
    def _tool_usage(qs, TaskStep, limit):
        from django.db.models import Count
        task_ids = list(qs.values_list("id", flat=True))
        rows = (
            TaskStep.objects.filter(task_id__in=task_ids, step_type=TaskStep.StepType.TOOL_CALL)
            .exclude(tool_name="")
            .values("tool_name")
            .annotate(n=Count("id"))
            .order_by("-n")[:limit]
        )
        return [{"tool": r["tool_name"], "calls": r["n"]} for r in rows]

    @staticmethod
    def _cost_usage(qs, TaskStep, limit):
        from django.db.models import Sum, Count
        agg = qs.aggregate(total_cost=Sum("cost_usd"), total_tokens=Sum("total_tokens"), n=Count("id"))
        by_agent = list(
            qs.exclude(agent__isnull=True)
            .values("agent__name")
            .annotate(cost=Sum("cost_usd"), tokens=Sum("total_tokens"))
            .order_by("-cost")[:limit]
        )
        return {
            "total_cost_usd": float(agg["total_cost"] or 0),
            "total_tokens": int(agg["total_tokens"] or 0),
            "task_count": agg["n"] or 0,
            "by_agent": [
                {"agent": r["agent__name"], "cost_usd": float(r["cost"] or 0), "tokens": int(r["tokens"] or 0)}
                for r in by_agent
            ],
        }

    # ------------------------------------------------------------------
    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "report_type": {
                        "type": "string",
                        "enum": [
                            "task_activity", "agent_activity", "email_activity", "meeting_activity",
                            "document_activity", "productivity", "execution_history", "success_failure",
                            "tool_usage", "cost_usage", "combined",
                        ],
                    },
                    "period": {
                        "type": "string",
                        "enum": ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "custom"],
                    },
                    "date_from": {"type": "string", "description": "YYYY-MM-DD, only used with period=custom"},
                    "date_to":   {"type": "string", "description": "YYYY-MM-DD, only used with period=custom"},
                    "agent_name": {"type": "string", "description": "Optional filter, e.g. 'Calendar Agent'"},
                    "limit": {"type": "integer", "description": "Max rows for list-style reports (default 50, max 200)"},
                },
                "required": ["report_type", "period"],
            },
        }}