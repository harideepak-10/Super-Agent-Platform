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
    # Write field — client sends limit_eur; we convert to limit_usd for storage
    limit_eur = serializers.DecimalField(
        max_digits=10, decimal_places=2, write_only=False, required=False,
        help_text="Budget limit in EUR. Converted to USD internally."
    )

    class Meta:
        model = Budget
        fields = [
            "id", "workspace", "period", "limit_eur",
            "alert_threshold_pct", "alert_status", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "workspace", "alert_status", "created_at", "updated_at"]

    def to_representation(self, instance):
        """Always return limit_eur (computed from the stored limit_usd)."""
        rep = super().to_representation(instance)
        rep["limit_eur"] = round(float(instance.limit_usd or 0) * _USD_TO_EUR, 2)
        return rep

    def validate_limit_eur(self, value):
        if value <= 0:
            raise serializers.ValidationError("Budget limit must be greater than zero.")
        return value

    def get_limit_usd_from_eur(self, validated_data):
        """Convert the incoming limit_eur to limit_usd for DB storage."""
        limit_eur = validated_data.pop("limit_eur", None)
        if limit_eur is not None:
            validated_data["limit_usd"] = round(float(limit_eur) / _USD_TO_EUR, 2)
        return validated_data
