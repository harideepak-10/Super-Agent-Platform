"""
Celery beat tasks for cost aggregation.
Runs every hour — aggregates task costs into DailyCost records.
"""
from celery import shared_task
from django.utils import timezone
from django.db.models import Sum, Count
import datetime


@shared_task(name="apps.costs.tasks.aggregate_daily_costs")
def aggregate_daily_costs():
    """
    Aggregate all completed task costs into DailyCost records.
    Runs every hour via Celery beat.
    """
    from apps.tasks.models import Task
    from apps.costs.models import DailyCost
    from apps.authentication.models import Workspace

    today = timezone.now().date()
    yesterday = today - datetime.timedelta(days=1)

    # Aggregate for today and yesterday (yesterday in case of late completions)
    for target_date in [today, yesterday]:
        workspaces = Workspace.objects.all()
        for workspace in workspaces:
            agg = Task.objects.filter(
                workspace=workspace,
                status="completed",
                completed_at__date=target_date,
            ).aggregate(
                total_cost=Sum("cost_usd"),
                task_count=Count("id"),
            )

            total_cost  = agg["total_cost"]  or 0
            task_count  = agg["task_count"]  or 0

            if task_count > 0:
                DailyCost.objects.update_or_create(
                    workspace=workspace,
                    date=target_date,
                    defaults={
                        "total_cost_usd": total_cost,
                        "task_count":     task_count,
                    },
                )

    return "Daily costs aggregated for %s" % today
