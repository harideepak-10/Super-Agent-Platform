import datetime

from django.db.models import Sum, Count
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import DailyCost, Budget
from .serializers import DailyCostSerializer, BudgetSerializer

# Fixed USD → EUR conversion rate
_USD_TO_EUR = 0.92


def _to_eur(usd_value):
    return round(float(usd_value or 0) * _USD_TO_EUR, 4)


def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


# ─────────────────────────────────────────────────────────────────────────────
# Cost Summary
# ─────────────────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def cost_summary(request):
    workspace = _get_workspace(request)
    today = timezone.now().date()
    month_start = today.replace(day=1)

    monthly = DailyCost.objects.filter(
        workspace=workspace, date__gte=month_start
    ).aggregate(
        total_cost=Sum("total_cost_usd"),
        total_tokens=Sum("total_tokens"),
        total_tasks=Sum("task_count"),
    )
    daily_today = DailyCost.objects.filter(workspace=workspace, date=today).first()
    budget = Budget.objects.filter(workspace=workspace).first()

    return Response({
        "monthly": {
            "total_cost_eur": _to_eur(monthly["total_cost"]),
            "total_tokens": monthly["total_tokens"] or 0,
            "total_tasks": monthly["total_tasks"] or 0,
        },
        "today": {
            "total_cost_eur": _to_eur(daily_today.total_cost_usd if daily_today else 0),
            "total_tokens": daily_today.total_tokens if daily_today else 0,
        },
        "budget": BudgetSerializer(budget).data if budget else None,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Daily Costs
# ─────────────────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def cost_daily(request):
    workspace = _get_workspace(request)
    days = int(request.query_params.get("days", 30))
    from_date = timezone.now().date() - datetime.timedelta(days=days)  # fix: datetime.timedelta not timezone.timedelta
    costs = DailyCost.objects.filter(workspace=workspace, date__gte=from_date).order_by("date")
    return Response(DailyCostSerializer(costs, many=True).data)


# ─────────────────────────────────────────────────────────────────────────────
# Budget — GET / POST (create only) / PATCH (update only)
# ─────────────────────────────────────────────────────────────────────────────

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def budget_detail(request):
    """
    GET  /costs/budget/  — return the workspace budget (null if none set)
    POST /costs/budget/  — create budget (400 if one already exists)
    """
    workspace = _get_workspace(request)

    if request.method == "GET":
        budget = Budget.objects.filter(workspace=workspace).first()
        return Response(BudgetSerializer(budget).data if budget else None)

    # POST — create only
    if Budget.objects.filter(workspace=workspace).exists():
        return Response(
            {"detail": "A budget already exists for this workspace. Use PATCH to update it."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = BudgetSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    budget = Budget.objects.create(
        workspace=workspace,
        period=Budget.Period.MONTHLY,
        limit_usd=serializer.validated_data["limit_usd"],
        alert_threshold_pct=serializer.validated_data.get("alert_threshold_pct", 80),
        created_by=request.user,
    )
    return Response(BudgetSerializer(budget).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def budget_update(request):
    """
    PATCH /costs/budget/update/  — update limit_usd or alert_threshold_pct
    Returns 404 if no budget has been created yet.
    """
    workspace = _get_workspace(request)
    budget = Budget.objects.filter(workspace=workspace).first()
    if not budget:
        return Response(
            {"detail": "No budget found. Create one first with POST /costs/budget/."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if "limit_usd" in request.data:
        budget.limit_usd = request.data["limit_usd"]
    if "alert_threshold_pct" in request.data:
        budget.alert_threshold_pct = request.data["alert_threshold_pct"]
    budget.save()
    return Response(BudgetSerializer(budget).data)


# ─────────────────────────────────────────────────────────────────────────────
# Cost by Agent
# ─────────────────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def cost_by_agent(request):
    from apps.tasks.models import Task

    workspace = _get_workspace(request)
    costs = (
        Task.objects.filter(workspace=workspace, status="completed")
        .values("agent__name", "agent_id")
        .annotate(
            total_cost_usd=Sum("cost_usd"),
            total_tokens=Sum("total_tokens"),
            task_count=Count("id"),
        )
        .order_by("-total_cost_usd")
    )
    result = []
    for row in costs:
        row["total_cost_eur"] = _to_eur(row.pop("total_cost_usd"))
        result.append(row)
    return Response(result)


# ─────────────────────────────────────────────────────────────────────────────
# Cost by Creator (me vs team members)
# ─────────────────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def cost_by_creator(request):
    """
    GET /costs/by-creator/

    Returns cost breakdown per person who ran tasks.
    Each row has a `is_me` flag so the frontend can highlight the current user.

    Optional query params:
      start_date  YYYY-MM-DD  (default: start of current month)
      end_date    YYYY-MM-DD  (default: today)
    """
    from apps.tasks.models import Task

    workspace = _get_workspace(request)
    today = timezone.now().date()
    month_start = today.replace(day=1)

    start_date = _parse_date(request.query_params.get("start_date"), month_start)
    end_date   = _parse_date(request.query_params.get("end_date"),   today)

    rows = (
        Task.objects.filter(
            workspace=workspace,
            status="completed",
            completed_at__date__gte=start_date,
            completed_at__date__lte=end_date,
        )
        .values(
            "created_by__id",
            "created_by__email",
            "created_by__name",
        )
        .annotate(
            total_cost_usd=Sum("cost_usd"),
            total_tokens=Sum("total_tokens"),
            task_count=Count("id"),
        )
        .order_by("-total_cost_usd")
    )

    result = []
    for row in rows:
        result.append({
            "user_id":      str(row["created_by__id"]),
            "name":         row["created_by__name"] or row["created_by__email"],
            "email":        row["created_by__email"],
            "is_me":        row["created_by__id"] == request.user.id,
            "total_cost_eur": _to_eur(row["total_cost_usd"]),
            "total_tokens": row["total_tokens"] or 0,
            "task_count":   row["task_count"],
        })

    # Summary split
    my_cost    = sum(r["total_cost_eur"] for r in result if r["is_me"])
    team_cost  = sum(r["total_cost_eur"] for r in result if not r["is_me"])
    total_cost = round(my_cost + team_cost, 4)

    return Response({
        "period": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "summary": {
            "total_cost_eur": total_cost,
            "my_cost_eur":    round(my_cost, 4),
            "team_cost_eur":  round(team_cost, 4),
            "my_pct":   round(my_cost / total_cost * 100, 1) if total_cost else 0.0,
            "team_pct": round(team_cost / total_cost * 100, 1) if total_cost else 0.0,
        },
        "breakdown": result,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Cost Range — by date range, grouped by agent | creator | workspace
# ─────────────────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def cost_range(request):
    """
    GET /costs/range/

    Query params:
      start_date   YYYY-MM-DD  required
      end_date     YYYY-MM-DD  required
      group_by     agent | creator | workspace   (default: agent)

    Returns a list of cost rows and a total, all in EUR.

    Examples:
      /costs/range/?start_date=2026-06-01&end_date=2026-07-04&group_by=agent
      /costs/range/?start_date=2026-06-01&end_date=2026-07-04&group_by=creator
      /costs/range/?start_date=2026-06-01&end_date=2026-07-04&group_by=workspace
    """
    from apps.tasks.models import Task

    workspace = _get_workspace(request)
    today = timezone.now().date()

    start_raw = request.query_params.get("start_date")
    end_raw   = request.query_params.get("end_date")
    group_by  = request.query_params.get("group_by", "agent")

    if not start_raw or not end_raw:
        return Response(
            {"detail": "Both start_date and end_date are required (YYYY-MM-DD)."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    start_date = _parse_date(start_raw, today)
    end_date   = _parse_date(end_raw,   today)

    if start_date > end_date:
        return Response(
            {"detail": "start_date must be before end_date."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    base_qs = Task.objects.filter(
        workspace=workspace,
        status="completed",
        completed_at__date__gte=start_date,
        completed_at__date__lte=end_date,
    )

    if group_by == "agent":
        rows = (
            base_qs
            .values("agent__id", "agent__name", "agent__agent_type")
            .annotate(
                total_cost_usd=Sum("cost_usd"),
                total_tokens=Sum("total_tokens"),
                task_count=Count("id"),
            )
            .order_by("-total_cost_usd")
        )
        result = [
            {
                "group_by": "agent",
                "agent_id":   str(r["agent__id"]) if r["agent__id"] else None,
                "agent_name": r["agent__name"] or "Unknown",
                "agent_type": r["agent__agent_type"] or "custom",
                "total_cost_eur": _to_eur(r["total_cost_usd"]),
                "total_tokens":   r["total_tokens"] or 0,
                "task_count":     r["task_count"],
            }
            for r in rows
        ]

    elif group_by == "creator":
        rows = (
            base_qs
            .values("created_by__id", "created_by__email", "created_by__name")
            .annotate(
                total_cost_usd=Sum("cost_usd"),
                total_tokens=Sum("total_tokens"),
                task_count=Count("id"),
            )
            .order_by("-total_cost_usd")
        )
        result = [
            {
                "group_by":  "creator",
                "user_id":   str(r["created_by__id"]),
                "name":      r["created_by__name"] or r["created_by__email"],
                "email":     r["created_by__email"],
                "is_me":     r["created_by__id"] == request.user.id,
                "total_cost_eur": _to_eur(r["total_cost_usd"]),
                "total_tokens":   r["total_tokens"] or 0,
                "task_count":     r["task_count"],
            }
            for r in rows
        ]

    elif group_by == "workspace":
        agg = base_qs.aggregate(
            total_cost_usd=Sum("cost_usd"),
            total_tokens=Sum("total_tokens"),
            task_count=Count("id"),
        )
        result = [
            {
                "group_by":       "workspace",
                "workspace_id":   str(workspace.id),
                "workspace_name": workspace.name,
                "total_cost_eur": _to_eur(agg["total_cost_usd"]),
                "total_tokens":   agg["total_tokens"] or 0,
                "task_count":     agg["task_count"] or 0,
            }
        ]

    else:
        return Response(
            {"detail": "group_by must be one of: agent, creator, workspace."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    total_eur = round(sum(r["total_cost_eur"] for r in result), 4)

    return Response({
        "period":   {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "group_by": group_by,
        "total_cost_eur": total_eur,
        "rows": result,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(value, fallback):
    """Parse YYYY-MM-DD string or return fallback date."""
    if not value:
        return fallback
    try:
        return datetime.date.fromisoformat(value)
    except (ValueError, TypeError):
        return fallback
