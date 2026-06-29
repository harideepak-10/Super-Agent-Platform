from rest_framework import serializers
from .models import DailyCost, Budget


class DailyCostSerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyCost
        fields = ["id", "workspace", "date", "total_cost_usd", "total_tokens", "task_count"]


class BudgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Budget
        fields = ["id", "workspace", "period", "limit_usd", "alert_threshold_pct",
                  "alert_status", "created_at", "updated_at"]
        read_only_fields = ["id", "workspace", "alert_status", "created_at", "updated_at"]
