from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import DailyCost, Budget
from .serializers import DailyCostSerializer, BudgetSerializer


def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


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
    budget = Budget.objects.filter(workspace=workspace, period=Budget.Period.MONTHLY).first()

    return Response({
        "monthly": {
            "total_cost_usd": monthly["total_cost"] or 0,
            "total_tokens": monthly["total_tokens"] or 0,
            "total_tasks": monthly["total_tasks"] or 0,
        },
        "today": {
            "total_cost_usd": str(daily_today.total_cost_usd) if daily_today else "0",
            "total_tokens": daily_today.total_tokens if daily_today else 0,
        },
        "budget": BudgetSerializer(budget).data if budget else None,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def cost_daily(request):
    workspace = _get_workspace(request)
    days = int(request.query_params.get("days", 30))
    from_date = timezone.now().date() - timezone.timedelta(days=days)
    costs = DailyCost.objects.filter(workspace=workspace, date__gte=from_date).order_by("date")
    return Response(DailyCostSerializer(costs, many=True).data)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def budget_detail(request):
    workspace = _get_workspace(request)
    if request.method == "GET":
        budgets = Budget.objects.filter(workspace=workspace)
        return Response(BudgetSerializer(budgets, many=True).data)

    serializer = BudgetSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    budget, created = Budget.objects.update_or_create(
        workspace=workspace,
        period=serializer.validated_data["period"],
        defaults={
            "limit_usd": serializer.validated_data["limit_usd"],
            "alert_threshold_pct": serializer.validated_data.get("alert_threshold_pct", 80),
            "created_by": request.user,
        },
    )
    return Response(BudgetSerializer(budget).data,
                    status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def cost_by_agent(request):
    from django.db.models import Sum, Count
    from apps.tasks.models import Task

    workspace = _get_workspace(request)
    costs = (
        Task.objects.filter(workspace=workspace, status="completed")
        .values("agent__name", "agent_id")
        .annotate(
            total_cost=Sum("cost_usd"),
            total_tokens=Sum("total_tokens"),
            task_count=Count("id"),
        )
        .order_by("-total_cost")
    )
    return Response(list(costs))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def cost_tracker(request):
    """
    GET /api/v1/costs/tracker/

    Cost Tracker screen — single call for all five sections:
      1. stat_cards  (this month, budget, avg per task, today)
      2. weekly_chart (Mon-Sun bar chart, current week)
      3. monthly_budget (progress bar)
      4. by_agent (breakdown list with percentage bars)
    """
    import datetime
    from django.db.models import Sum, Count, Avg
    from apps.tasks.models import Task

    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace."}, status=status.HTTP_400_BAD_REQUEST)

    today = timezone.now().date()
    month_start = today.replace(day=1)
    last_month_start = (month_start - datetime.timedelta(days=1)).replace(day=1)
    last_month_end = month_start - datetime.timedelta(days=1)

    # ── 1. Aggregate monthly / today ─────────────────────────────────────────
    monthly_agg = DailyCost.objects.filter(
        workspace=workspace, date__gte=month_start
    ).aggregate(
        total_cost=Sum("total_cost_usd"),
        total_tasks=Sum("task_count"),
    )
    monthly_cost  = float(monthly_agg["total_cost"] or 0)
    monthly_tasks = int(monthly_agg["total_tasks"] or 0)

    daily_today = DailyCost.objects.filter(workspace=workspace, date=today).first()
    today_cost  = float(daily_today.total_cost_usd) if daily_today else 0.0
    today_tasks = int(daily_today.task_count) if daily_today else 0

    # Avg per task this month
    avg_this_month = (monthly_cost / monthly_tasks) if monthly_tasks else 0.0

    # Avg per task last month (for delta)
    last_month_agg = DailyCost.objects.filter(
        workspace=workspace, date__gte=last_month_start, date__lte=last_month_end
    ).aggregate(total_cost=Sum("total_cost_usd"), total_tasks=Sum("task_count"))
    lm_cost  = float(last_month_agg["total_cost"] or 0)
    lm_tasks = int(last_month_agg["total_tasks"] or 0)
    avg_last_month = (lm_cost / lm_tasks) if lm_tasks else 0.0

    if avg_last_month > 0:
        avg_delta_pct = round((avg_this_month - avg_last_month) / avg_last_month * 100, 1)
    else:
        avg_delta_pct = 0.0

    # ── 2. Budget ─────────────────────────────────────────────────────────────
    budget = Budget.objects.filter(workspace=workspace, period=Budget.Period.MONTHLY).first()
    budget_limit = float(budget.limit_usd) if budget else 0.0
    pct_used     = round(monthly_cost / budget_limit * 100, 1) if budget_limit else 0.0
    pct_remaining = round(100 - pct_used, 1) if budget_limit else 100.0
    remaining    = round(budget_limit - monthly_cost, 2) if budget_limit else 0.0

    # ── 3. Weekly chart (Mon–Sun of the current ISO week) ────────────────────
    weekday = today.weekday()   # Mon=0
    week_monday = today - datetime.timedelta(days=weekday)
    week_days = [week_monday + datetime.timedelta(days=i) for i in range(7)]

    daily_rows = {
        row.date: row
        for row in DailyCost.objects.filter(
            workspace=workspace,
            date__gte=week_monday,
            date__lte=week_days[-1],
        )
    }

    day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekly_bars = []
    week_total = 0.0
    for i, d in enumerate(week_days):
        row = daily_rows.get(d)
        cost = float(row.total_cost_usd) if row else 0.0
        week_total += cost
        weekly_bars.append({
            "day":       day_labels[i],
            "date":      d.isoformat(),
            "cost":      round(cost, 2),
            "label":     "€%.2f" % cost,
            "is_today":  d == today,
        })

    # ── 4. By-agent breakdown ─────────────────────────────────────────────────
    agent_rows = (
        Task.objects
        .filter(workspace=workspace, created_at__date__gte=month_start)
        .exclude(agent=None)
        .values("agent__id", "agent__name", "agent__agent_type")
        .annotate(
            total_cost=Sum("cost_usd"),
            task_count=Count("id"),
        )
        .order_by("-total_cost")
    )

    by_agent = []
    for row in agent_rows:
        agent_cost = float(row["total_cost"] or 0)
        pct_of_total = round(agent_cost / monthly_cost * 100, 1) if monthly_cost else 0.0
        by_agent.append({
            "agent_id":    str(row["agent__id"]),
            "agent_name":  row["agent__name"],
            "agent_type":  row["agent__agent_type"],
            "cost":        round(agent_cost, 2),
            "cost_label":  "€%.2f" % agent_cost,
            "task_count":  row["task_count"],
            "pct_of_total": pct_of_total,
            "bar_label":   "%d tasks · %d%% of total" % (row["task_count"], int(pct_of_total)),
        })

    return Response({
        # Header subtitle
        "header_subtitle": "€%.2f spent this month" % monthly_cost,

        # ── Section 1: Stat cards ──────────────────────────────────────────
        "stat_cards": {
            "this_month": {
                "cost":        round(monthly_cost, 2),
                "cost_label":  "€%.2f" % monthly_cost,
                "tasks_run":   monthly_tasks,
                "sub_label":   "%d tasks run" % monthly_tasks,
            },
            "budget": {
                "limit":           round(budget_limit, 2),
                "limit_label":     "€%.2f" % budget_limit,
                "pct_remaining":   pct_remaining,
                "sub_label":       "%.0f%% remaining" % pct_remaining,
                "alert_status":    budget.alert_status if budget else "ok",
            },
            "avg_per_task": {
                "avg":         round(avg_this_month, 3),
                "avg_label":   "€%.3f" % avg_this_month,
                "delta_pct":   avg_delta_pct,
                "delta_label": "%s%.0f%% vs last month" % (
                    "+" if avg_delta_pct >= 0 else "", avg_delta_pct
                ),
                "trend":       "up" if avg_delta_pct > 0 else ("down" if avg_delta_pct < 0 else "flat"),
            },
            "today": {
                "cost":       round(today_cost, 2),
                "cost_label": "€%.2f" % today_cost,
                "tasks_run":  today_tasks,
                "sub_label":  "%d tasks run" % today_tasks,
            },
        },

        # ── Section 2: Weekly bar chart ────────────────────────────────────
        "weekly_chart": {
            "days":        weekly_bars,
            "week_total":  round(week_total, 2),
            "total_label": "€%.2f total" % week_total,
        },

        # ── Section 3: Monthly budget progress bar ─────────────────────────
        "monthly_budget": {
            "spent":          round(monthly_cost, 2),
            "limit":          round(budget_limit, 2),
            "remaining":      remaining,
            "pct_used":       pct_used,
            "spent_label":    "€%.2f" % monthly_cost,
            "limit_label":    "€%.2f" % budget_limit,
            "remaining_label": "€%.2f remaining" % remaining,
            "pct_label":      "%.0f%% used" % pct_used,
            "bar_color":      "#EF4444" if pct_used >= 95 else ("#F59E0B" if pct_used >= 80 else "#22C55E"),
        },

        # ── Section 4: By-agent breakdown ──────────────────────────────────
        "by_agent": by_agent,
    })
