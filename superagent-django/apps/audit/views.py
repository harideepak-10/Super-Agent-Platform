import math
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .models import AuditEvent
from .serializers import AuditEventSerializer


def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_list(request):
    workspace = _get_workspace(request)
    events = AuditEvent.objects.filter(workspace=workspace).order_by("-created_at")

    event_type = request.query_params.get("event_type")
    if event_type:
        events = events.filter(event_type=event_type)

    resource_type = request.query_params.get("resource_type")
    resource_id = request.query_params.get("resource_id")
    if resource_type:
        events = events.filter(resource_type=resource_type)
    if resource_id:
        events = events.filter(resource_id=resource_id)

    actor_id = request.query_params.get("actor_id")
    if actor_id:
        events = events.filter(actor_id=actor_id)

    # Date range
    from_date = request.query_params.get("from")
    to_date = request.query_params.get("to")
    if from_date:
        events = events.filter(created_at__date__gte=from_date)
    if to_date:
        events = events.filter(created_at__date__lte=to_date)

    page_size = min(int(request.query_params.get("page_size", 50)), 200)
    events = events[:page_size]
    return Response(AuditEventSerializer(events, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_event_types(request):
    return Response([
        {"value": choice[0], "label": choice[1]}
        for choice in AuditEvent.EventType.choices
    ])


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_summary(request):
    from django.db.models import Count
    workspace = _get_workspace(request)
    summary = (
        AuditEvent.objects
        .filter(workspace=workspace)
        .values("event_type")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    return Response(list(summary))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_by_resource(request, resource_type, resource_id):
    workspace = _get_workspace(request)
    events = AuditEvent.objects.filter(
        workspace=workspace,
        resource_type=resource_type,
        resource_id=resource_id,
    ).order_by("-created_at")
    return Response(AuditEventSerializer(events, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_actor(request, actor_id):
    workspace = _get_workspace(request)
    events = AuditEvent.objects.filter(
        workspace=workspace, actor_id=actor_id
    ).order_by("-created_at")[:100]
    return Response(AuditEventSerializer(events, many=True).data)


# ---------------------------------------------------------------------------
# Task Audit Timeline
# ---------------------------------------------------------------------------

def _fmt_time(dt):
    """Format datetime as HH:MM:SS string."""
    return dt.strftime("%H:%M:%S") if dt else None


def _duration_label(start, end):
    """Return human-readable duration like '8m 35s'."""
    if not start or not end:
        return None
    secs = int((end - start).total_seconds())
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    return f"{m}m {s}s" if s else f"{m}m"


def _cost_label(cost_usd):
    """Convert USD cost to euro label like '€0.33'."""
    eur = float(cost_usd or 0) * 0.92
    if eur == 0:
        return "€0.00"
    if eur < 0.01:
        return f"€{eur:.4f}"
    return f"€{eur:.2f}"


def _icon_for_agent(agent_type: str) -> str:
    return {
        "email":      "mail",
        "calendar":   "calendar",
        "document":   "file-text",
        "research":   "search",
        "finance":    "wallet",
        "reporting":  "bar-chart",
        "compliance": "shield",
        "orchestrator": "git-branch",
    }.get(agent_type, "cpu")


def _status_label_for_step(step_type: str, tool_zone: str, tool_name: str) -> str:
    if step_type == "final_answer":
        return "done"
    if step_type == "thought":
        return "thinking"
    if step_type == "tool_call":
        if tool_zone == "yellow":
            return "wait"
        return "running"
    if step_type == "tool_result":
        return "done"
    return "done"


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def task_audit_timeline(request, task_id):
    """
    GET /api/v1/audit/task/<task_id>/timeline/

    Returns a structured timeline for the Task Audit screen.

    Response::

        {
            "task": {
                "id":         "...",
                "prompt":     "Read my latest 3 emails and summarise...",
                "status":     "completed",
                "start_time": "09:00",
                "end_time":   "09:08",
                "duration":   "8m 35s",
                "cost":       "€0.33",
                "agent_name": "Email Agent",
                "agent_type": "email"
            },
            "timeline": [
                {
                    "id":           "...",
                    "actor":        "Shereena",
                    "actor_type":   "user",         // "user" | "agent" | "system"
                    "icon":         "user",
                    "action":       "Created task",
                    "detail":       "mobile app",
                    "time":         "09:00:14",
                    "status_label": "created",
                    "status_color": "#3B82F6"
                },
                {
                    "actor":        "Email Agent",
                    "actor_type":   "agent",
                    "icon":         "mail",
                    "action":       "Reading emails",
                    "detail":       "Fetching latest 3 unread emails",
                    "time":         "09:00:38",
                    "status_label": "done",
                    "status_color": "#10B981"
                },
                ...
            ]
        }
    """
    from apps.tasks.models import Task, TaskStep
    from apps.approvals.models import Approval

    workspace = _get_workspace(request)

    try:
        task = Task.objects.select_related("agent", "created_by").get(
            id=task_id, workspace=workspace
        )
    except Task.DoesNotExist:
        return Response({"detail": "Task not found."}, status=status.HTTP_404_NOT_FOUND)

    steps    = TaskStep.objects.filter(task=task).order_by("step_number")
    approvals = Approval.objects.filter(task=task).order_by("created_at") if hasattr(task, "approvals") else []

    # ── Status color map ──────────────────────────────────────────────────────
    _COLOR = {
        "created":  "#6B7280",
        "started":  "#3B82F6",
        "thinking": "#8B5CF6",
        "running":  "#3B82F6",
        "done":     "#10B981",
        "wait":     "#F59E0B",
        "approved": "#10B981",
        "rejected": "#EF4444",
        "failed":   "#EF4444",
        "complete": "#10B981",
    }

    timeline = []

    # ── Event 1: Task created ─────────────────────────────────────────────────
    user_name = task.created_by.name if task.created_by else "User"
    timeline.append({
        "id":           f"created-{task.id}",
        "actor":        user_name,
        "actor_type":   "user",
        "icon":         "user",
        "action":       "Created task",
        "detail":       "via app",
        "time":         _fmt_time(task.created_at),
        "status_label": "created",
        "status_color": _COLOR["created"],
    })

    # ── Event 2: Task started ─────────────────────────────────────────────────
    if task.started_at:
        agent_name = task.agent.name if task.agent else "Agent"
        agent_type = task.agent.agent_type if task.agent else ""
        timeline.append({
            "id":           f"started-{task.id}",
            "actor":        agent_name,
            "actor_type":   "agent",
            "icon":         _icon_for_agent(agent_type),
            "action":       "Task started",
            "detail":       f"{agent_name} began processing",
            "time":         _fmt_time(task.started_at),
            "status_label": "started",
            "status_color": _COLOR["started"],
        })

    # ── Steps: tool calls ─────────────────────────────────────────────────────
    seen_tools = set()
    for step in steps:
        # Only show tool_call steps (skip thoughts and tool_results to avoid duplication)
        if step.step_type not in ("tool_call", "final_answer"):
            continue

        tool_label = step.title or step.tool_name or step.content[:60]
        detail     = step.detail or ""
        agent_name = step.agent_name or (task.agent.name if task.agent else "Agent")
        agent_type = task.agent.agent_type if task.agent else ""

        if step.step_type == "final_answer":
            sl = "complete"
        elif step.tool_zone == "yellow":
            sl = "wait"
        else:
            sl = "done"

        timeline.append({
            "id":           str(step.id),
            "actor":        agent_name,
            "actor_type":   "agent",
            "icon":         _icon_for_agent(agent_type),
            "action":       tool_label,
            "detail":       detail,
            "time":         _fmt_time(step.created_at),
            "status_label": sl,
            "status_color": _COLOR.get(sl, "#6B7280"),
            "tool_name":    step.tool_name,
            "tool_zone":    step.tool_zone,
        })

    # ── Approval events ───────────────────────────────────────────────────────
    try:
        from apps.approvals.models import Approval
        for appr in Approval.objects.filter(task=task).order_by("created_at"):
            # Waiting event
            timeline.append({
                "id":           f"wait-{appr.id}",
                "actor":        agent_name if task.agent else "Agent",
                "actor_type":   "agent",
                "icon":         "pause-circle",
                "action":       "Waiting for approval",
                "detail":       appr.description or appr.tool_name or "",
                "time":         _fmt_time(appr.created_at),
                "status_label": "wait",
                "status_color": _COLOR["wait"],
            })
            # Decision event
            if appr.decided_at and appr.decision in ("approved", "rejected"):
                reviewer_name = appr.decided_by.name if appr.decided_by else "User"
                sl = appr.decision
                timeline.append({
                    "id":           f"decision-{appr.id}",
                    "actor":        reviewer_name,
                    "actor_type":   "user",
                    "icon":         "user",
                    "action":       f"{reviewer_name} {appr.decision}",
                    "detail":       appr.rejection_reason or "",
                    "time":         _fmt_time(appr.decided_at),
                    "status_label": sl,
                    "status_color": _COLOR.get(sl, "#6B7280"),
                })
    except Exception:
        pass

    # ── Final event: completed / failed ───────────────────────────────────────
    if task.completed_at and task.status in ("completed", "failed"):
        sl     = "complete" if task.status == "completed" else "failed"
        dur    = _duration_label(task.started_at or task.created_at, task.completed_at)
        cost   = _cost_label(task.cost_usd)
        detail = f"{dur} · {cost}" if dur else cost
        timeline.append({
            "id":           f"completed-{task.id}",
            "actor":        task.agent.name if task.agent else "System",
            "actor_type":   "agent",
            "icon":         "check-circle" if task.status == "completed" else "x-circle",
            "action":       "Task complete" if task.status == "completed" else "Task failed",
            "detail":       detail,
            "time":         _fmt_time(task.completed_at),
            "status_label": sl,
            "status_color": _COLOR.get(sl, "#10B981"),
        })

    # Sort timeline by time (in case approvals inserted out of order)
    timeline.sort(key=lambda e: e["time"] or "")

    return Response({
        "task": {
            "id":         str(task.id),
            "prompt":     task.prompt,
            "status":     task.status,
            "start_time": _fmt_time(task.started_at or task.created_at),
            "end_time":   _fmt_time(task.completed_at),
            "duration":   _duration_label(task.started_at or task.created_at, task.completed_at),
            "cost":       _cost_label(task.cost_usd),
            "agent_name": task.agent.name if task.agent else None,
            "agent_type": task.agent.agent_type if task.agent else None,
        },
        "timeline": timeline,
    })
