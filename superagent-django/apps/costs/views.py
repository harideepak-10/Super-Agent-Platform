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
    from apps.tasks.models import Task
    from django.db.models import Count

    workspace = _get_workspace(request)
    today     = timezone.now().date()
    month_start = today.replace(day=1)

    # Last month range (for avg comparison)
    last_month_end   = month_start - datetime.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    # ── Monthly totals ────────────────────────────────────────────────────────
    monthly = DailyCost.objects.filter(
        workspace=workspace, date__gte=month_start
    ).aggregate(total_cost=Sum("total_cost_usd"), total_tasks=Sum("task_count"))
    monthly_cost_eur = _to_eur(monthly["total_cost"])
    monthly_tasks    = monthly["total_tasks"] or 0

    # ── Last month totals ─────────────────────────────────────────────────────
    last_month = DailyCost.objects.filter(
        workspace=workspace,
        date__gte=last_month_start,
        date__lte=last_month_end,
    ).aggregate(total_cost=Sum("total_cost_usd"), total_tasks=Sum("task_count"))
    last_month_cost_eur = _to_eur(last_month["total_cost"])
    last_month_tasks    = last_month["total_tasks"] or 0

    # ── Today ─────────────────────────────────────────────────────────────────
    daily_today    = DailyCost.objects.filter(workspace=workspace, date=today).first()
    today_cost_eur = _to_eur(daily_today.total_cost_usd if daily_today else 0)
    today_tasks    = daily_today.task_count if daily_today else 0

    # ── Budget ────────────────────────────────────────────────────────────────
    budget    = Budget.objects.filter(workspace=workspace).first()
    limit_eur = round(float(budget.limit_usd) * _USD_TO_EUR, 2) if budget else None

    # ── Avg per task + month-over-month change ────────────────────────────────
    avg_this = round(monthly_cost_eur / monthly_tasks, 4) if monthly_tasks else 0.0
    avg_last = round(last_month_cost_eur / last_month_tasks, 4) if last_month_tasks else 0.0
    if avg_last:
        change_pct = round((avg_this - avg_last) / avg_last * 100, 1)
    else:
        change_pct = 0.0
    change_direction = "up" if change_pct > 0 else ("down" if change_pct < 0 else "flat")
    if change_pct != 0:
        change_label = "%s %.1f%% vs last month" % ("↑" if change_pct > 0 else "↓", abs(change_pct))
    else:
        change_label = "— same as last month"

    # ── Budget percentages ────────────────────────────────────────────────────
    if limit_eur:
        pct_used      = round(monthly_cost_eur / limit_eur * 100, 1)
        pct_remaining = round(100 - pct_used, 1)
        remaining_eur = round(limit_eur - monthly_cost_eur, 2)
    else:
        pct_used = pct_remaining = remaining_eur = None

    # ── This week (Mon–Sun) ───────────────────────────────────────────────────
    week_start = today - datetime.timedelta(days=today.weekday())   # Monday
    week_end   = week_start + datetime.timedelta(days=6)            # Sunday

    week_costs = {
        dc.date: dc.total_cost_usd
        for dc in DailyCost.objects.filter(
            workspace=workspace, date__gte=week_start, date__lte=week_end
        )
    }
    _DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    week_days      = []
    week_total_eur = 0.0
    for i in range(7):
        d    = week_start + datetime.timedelta(days=i)
        cost = _to_eur(week_costs.get(d, 0))
        week_total_eur += cost
        week_days.append({
            "date":      d.isoformat(),
            "day_label": _DAY_LABELS[i],
            "cost_eur":  cost,
            "cost_label": "€%.2f" % cost,
            "is_today":  d == today,
        })
    week_total_eur = round(week_total_eur, 2)

    # ── By agent (this month, all task statuses with a cost) ─────────────────
    _AGENT_ICONS = {
        "email": "mail", "calendar": "calendar", "document": "file-text",
        "research": "search", "finance": "wallet", "reporting": "bar-chart",
        "compliance": "shield", "qa": "check-square", "orchestrator": "git-branch",
        "custom": "cpu",
    }
    agent_rows = (
        Task.objects.filter(workspace=workspace, created_at__date__gte=month_start)
        .exclude(cost_usd=None)
        .values("agent__id", "agent__name", "agent__agent_type")
        .annotate(total_cost_usd=Sum("cost_usd"), task_count=Count("id"))
        .order_by("-total_cost_usd")
    )
    denom = monthly_cost_eur or 1
    by_agent = []
    for row in agent_rows:
        cost_eur   = _to_eur(row["total_cost_usd"])
        pct        = round(cost_eur / denom * 100, 1)
        agent_type = row["agent__agent_type"] or "custom"
        n          = row["task_count"]
        by_agent.append({
            "agent_id":     str(row["agent__id"]) if row["agent__id"] else None,
            "name":         row["agent__name"] or "Unknown",
            "agent_type":   agent_type,
            "icon":         _AGENT_ICONS.get(agent_type, "cpu"),
            "cost_eur":     cost_eur,
            "cost_label":   "€%.2f" % cost_eur,
            "task_count":   n,
            "pct_of_total": pct,
            "task_label":   "%d task%s · %s%% of total" % (n, "s" if n != 1 else "", pct),
        })

    # ── Response ──────────────────────────────────────────────────────────────
    return Response({
        "header": {
            "this_month": {
                "cost_eur":    monthly_cost_eur,
                "cost_label":  "€%.2f" % monthly_cost_eur,
                "tasks":       monthly_tasks,
                "tasks_label": "%d task%s run" % (monthly_tasks, "s" if monthly_tasks != 1 else ""),
            },
            "budget": {
                "limit_eur":       limit_eur,
                "limit_label":     "€%.2f" % limit_eur if limit_eur else None,
                "pct_remaining":   pct_remaining,
                "remaining_label": "%s%% remaining" % pct_remaining if pct_remaining is not None else None,
            } if budget else None,
            "avg_per_task": {
                "cost_eur":         avg_this,
                "cost_label":       "€%.3f" % avg_this,
                "change_pct":       change_pct,
                "change_label":     change_label,
                "change_direction": change_direction,
            },
            "today": {
                "cost_eur":    today_cost_eur,
                "cost_label":  "€%.2f" % today_cost_eur,
                "tasks":       today_tasks,
                "tasks_label": "%d task%s run" % (today_tasks, "s" if today_tasks != 1 else ""),
            },
        },
        "monthly_budget": {
            "spent_eur":      monthly_cost_eur,
            "limit_eur":      limit_eur,
            "pct_used":       pct_used,
            "pct_remaining":  pct_remaining,
            "remaining_eur":  remaining_eur,
            "spent_label":    ("€%.2f of €%.2f" % (monthly_cost_eur, limit_eur)) if limit_eur else ("€%.2f" % monthly_cost_eur),
            "pct_used_label": "%s%% used" % pct_used if pct_used is not None else None,
            "remaining_label": "€%.2f remaining" % remaining_eur if remaining_eur is not None else None,
        },
        "by_agent": by_agent,
        "this_week": {
            "total_eur":   week_total_eur,
            "total_label": "€%.2f total" % week_total_eur,
            "days":        week_days,
        },
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
    vd = serializer.get_limit_usd_from_eur(dict(serializer.validated_data))
    budget = Budget.objects.create(
        workspace=workspace,
        period=Budget.Period.MONTHLY,
        limit_usd=vd["limit_usd"],
        alert_threshold_pct=vd.get("alert_threshold_pct", 80),
        created_by=request.user,
    )
    return Response(BudgetSerializer(budget).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def budget_update(request):
    """
    PATCH /costs/budget/update/  — update limit_eur or alert_threshold_pct
    Returns 404 if no budget has been created yet.
    """
    workspace = _get_workspace(request)
    budget = Budget.objects.filter(workspace=workspace).first()
    if not budget:
        return Response(
            {"detail": "No budget found. Create one first with POST /costs/budget/."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if "limit_eur" in request.data:
        try:
            limit_eur = float(request.data["limit_eur"])
            if limit_eur <= 0:
                return Response({"detail": "limit_eur must be greater than zero."}, status=status.HTTP_400_BAD_REQUEST)
            budget.limit_usd = round(limit_eur / 0.92, 2)
        except (ValueError, TypeError):
            return Response({"detail": "limit_eur must be a valid number."}, status=status.HTTP_400_BAD_REQUEST)
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
