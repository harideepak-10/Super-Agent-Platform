from rest_framework import serializers
from .models import DailyCost, Budget

_USD_TO_EUR = 0.92


class DailyCostSerializer(serializers.ModelSerializer):
    total_cost_eur = serializers.SerializerMethodField()

    class Meta:
        model = DailyCost
        fields = ["id", "workspace", "date", "total_cost_eur", "total_tokens", "task_count"]

    def get_total_cost_eur(self, obj):
        return round(float(obj.total_cost_usd or 0) * _USD_TO_EUR, 4)


class BudgetSerializer(serializers.ModelSerializer):
    limit_eur = serializers.SerializerMethodField()

    class Meta:
        model = Budget
        fields = [
            "id", "workspace", "period", "limit_usd", "limit_eur",
            "alert_threshold_pct", "alert_status", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "workspace", "limit_eur", "alert_status", "created_at", "updated_at"]

    def get_limit_eur(self, obj):
        return round(float(obj.limit_usd or 0) * _USD_TO_EUR, 4)
