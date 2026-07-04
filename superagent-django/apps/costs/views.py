from django.db.models import Sum
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
            "total_cost_eur": round(_to_eur(monthly["total_cost"]), 4),
            "total_tokens": monthly["total_tokens"] or 0,
            "total_tasks": monthly["total_tasks"] or 0,
        },
        "today": {
            "total_cost_eur": _to_eur(daily_today.total_cost_usd if daily_today else 0),
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
            total_cost_usd=Sum("cost_usd"),
            total_tokens=Sum("total_tokens"),
            task_count=Count("id"),
        )
        .order_by("-total_cost_usd")
    )
    result = []
    for row in costs:
        row["total_cost_eur"] = round(float(row.pop("total_cost_usd") or 0) * _USD_TO_EUR, 4)
        result.append(row)
    return Response(result)
