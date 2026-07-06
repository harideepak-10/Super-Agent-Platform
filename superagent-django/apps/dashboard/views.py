"""
Dashboard API — single endpoint that powers the home screen.

GET /api/v1/dashboard/
Returns: greeting, 4 stat cards, urgent approvals, recent activity feed.
"""

from datetime import date, timedelta, datetime, timezone

from django.db.models import Sum, Count, Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


def _greeting(user):
    """Return time-of-day greeting and day string."""
    now = datetime.now(timezone.utc)
    hour = now.hour
    if hour < 12:
        period = "morning"
    elif hour < 17:
        period = "afternoon"
    else:
        period = "evening"

    return {
        "name": user.name or user.email.split("@")[0],
        "time_of_day": period,
        "full_greeting": f"Good {period}, {user.name or user.email.split('@')[0]}",
        "day": now.strftime("%A"),
        "date": now.date().isoformat(),
    }


def _tasks_today_stats(workspace):
    """Tasks created today vs yesterday delta."""
    from apps.tasks.models import Task

    today = date.today()
    yesterday = today - timedelta(days=1)

    today_count = Task.objects.filter(
        workspace=workspace,
        created_at__date=today,
    ).exclude(status=Task.Status.CANCELLED).count()

    yesterday_count = Task.objects.filter(
        workspace=workspace,
        created_at__date=yesterday,
    ).exclude(status=Task.Status.CANCELLED).count()

    delta = today_count - yesterday_count

    return {
        "count": today_count,
        "delta_from_yesterday": delta,
        "delta_label": f"+{delta} from yesterday" if delta >= 0 else f"{delta} from yesterday",
    }


def _approval_stats(workspace):
    """Count of pending approvals."""
    from apps.approvals.models import Approval

    count = Approval.objects.filter(
        task__workspace=workspace,
        status=Approval.Status.PENDING,
    ).count()

    return {
        "count": count,
        "has_urgent": count > 0,
    }


def _agent_stats(workspace):
    """Active agents and health status."""
    from apps.agents.models import Agent
    from apps.tasks.models import Task

    active_agents = Agent.objects.filter(workspace=workspace, is_active=True)
    total_active = active_agents.count()

    # An agent is "unhealthy" if it has a task that failed in the last hour
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    unhealthy_agent_ids = set(
        Task.objects.filter(
            workspace=workspace,
            status=Task.Status.FAILED,
            completed_at__gte=one_hour_ago,
            agent__isnull=False,
        ).values_list("agent_id", flat=True)
    )

    running_count = Task.objects.filter(
        workspace=workspace,
        status=Task.Status.RUNNING,
    ).count()

    return {
        "total_active": total_active,
        "running_tasks": running_count,
        "unhealthy_count": len(unhealthy_agent_ids),
        "all_healthy": len(unhealthy_agent_ids) == 0,
        "status_label": "All healthy" if len(unhealthy_agent_ids) == 0 else f"{len(unhealthy_agent_ids)} needs attention",
    }


def _cost_today_stats(workspace):
    """Today's cost vs budget."""
    from apps.costs.models import DailyCost, Budget

    today = date.today()

    _USD_TO_EUR = 0.92
    daily = DailyCost.objects.filter(workspace=workspace, date=today).first()
    cost_today_usd = float(daily.total_cost_usd) if daily else 0.0
    cost_today = round(cost_today_usd * _USD_TO_EUR, 4)

    budget = Budget.objects.filter(workspace=workspace).order_by("-created_at").first()
    limit = round(float(budget.limit_usd) * _USD_TO_EUR, 2) if budget else 0.0
    period = budget.period if budget else None
    limit_usd = float(budget.limit_usd) if budget else 0.0

    pct_used = round((cost_today_usd / limit_usd * 100), 1) if limit_usd > 0 else 0.0
    alert_status = "ok"
    if limit_usd > 0:
        if pct_used >= 95:
            alert_status = "critical"
        elif pct_used >= 80:
            alert_status = "warning"

    return {
        "amount": cost_today,
        "currency": "EUR",
        "limit": limit,
        "limit_period": period,
        "percentage_used": pct_used,
        "alert_status": alert_status,
        "label": f"€{cost_today:.2f}" + (f" of €{limit:.0f} limit" if limit > 0 else ""),
    }


def _urgent_approvals(workspace, limit=2):
    """Most recent pending approvals — shown as action cards on home screen."""
    from apps.approvals.models import Approval

    approvals = (
        Approval.objects
        .filter(task__workspace=workspace, status=Approval.Status.PENDING)
        .select_related("task", "task__agent")
        .order_by("created_at")[:limit]
    )

    result = []
    for ap in approvals:
        age_seconds = (datetime.now(timezone.utc) - ap.created_at).total_seconds()
        agent_name = ap.task.agent.name if ap.task.agent else "Agent"
        result.append({
            "id": str(ap.id),
            "task_id": str(ap.task_id),
            "agent_name": agent_name,
            "agent_type": ap.task.agent.agent_type if ap.task.agent else "custom",
            "message": f"{agent_name} wants your approval",
            "tool_name": ap.tool_name,
            "tool_input": ap.tool_input,
            "task_prompt": ap.task.prompt[:120],
            "requested_ago": _human_time(age_seconds),
            "requested_ago_seconds": int(age_seconds),
            "is_urgent": age_seconds > 300,  # urgent if waiting > 5 min
            "created_at": ap.created_at.isoformat(),
        })

    return result


def _recent_activity(workspace, limit=10):
    """Recent agent actions for the activity feed."""
    from apps.tasks.models import Task

    tasks = (
        Task.objects
        .filter(workspace=workspace)
        .exclude(status__in=[Task.Status.QUEUED, Task.Status.RUNNING])
        .select_related("agent")
        .order_by("-updated_at")[:limit]
    )

    STATUS_VERB = {
        Task.Status.COMPLETED:        ("completed", "success"),
        Task.Status.FAILED:           ("failed",    "error"),
        Task.Status.CANCELLED:        ("cancelled", "neutral"),
        Task.Status.WAITING_APPROVAL: ("paused — needs approval", "warning"),
    }

    AGENT_ICON = {
        "email":      "mail",
        "finance":    "chart-bar",
        "document":   "file",
        "reporting":  "report",
        "compliance": "shield-check",
        "qa":         "checklist",
        "custom":     "robot",
    }

    result = []
    for task in tasks:
        verb, status_type = STATUS_VERB.get(task.status, ("updated", "neutral"))
        age_seconds = (datetime.now(timezone.utc) - task.updated_at).total_seconds()
        agent_type = task.agent.agent_type if task.agent else "custom"

        result.append({
            "task_id": str(task.id),
            "agent_name": task.agent.name if task.agent else "Agent",
            "agent_type": agent_type,
            "icon": AGENT_ICON.get(agent_type, "robot"),
            "status": task.status,
            "status_type": status_type,       # success | error | warning | neutral
            "verb": verb,
            "summary": task.result[:100] if task.result else task.prompt[:80],
            "task_prompt": task.prompt[:120],
            "time_ago": _human_time(age_seconds),
            "time_ago_seconds": int(age_seconds),
            "updated_at": task.updated_at.isoformat(),
            "steps_taken": task.steps_taken,
            "cost_eur": round(float(task.cost_usd or 0) * 0.92, 4),
        })

    return result


def _human_time(seconds):
    """Convert seconds to human-readable 'X min ago' / 'X hours ago'."""
    seconds = int(seconds)
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        m = seconds // 60
        return f"{m} min ago"
    elif seconds < 86400:
        h = seconds // 3600
        return f"{h} hour{'s' if h > 1 else ''} ago"
    else:
        d = seconds // 86400
        return f"{d} day{'s' if d > 1 else ''} ago"


# ─── Main endpoint ───────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dashboard(request):
    """
    GET /api/v1/dashboard/

    Returns all data needed to render the home screen in a single call:
    - greeting (name, time of day, day/date)
    - stats (tasks today, pending approvals, agents running, cost today)
    - urgent_approvals (pending approval cards, ordered oldest-first)
    - recent_activity (last N completed/failed tasks across all agents)
    """
    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace found."}, status=400)

    return Response({
        "greeting":         _greeting(request.user),
        "stats": {
            "tasks_today":    _tasks_today_stats(workspace),
            "need_approval":  _approval_stats(workspace),
            "agents_running": _agent_stats(workspace),
            "cost_today":     _cost_today_stats(workspace),
        },
        "urgent_approvals": _urgent_approvals(workspace, limit=2),
        "recent_activity":  _recent_activity(workspace, limit=3),
    })
