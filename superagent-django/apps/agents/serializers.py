from rest_framework import serializers
from .models import Agent

_USD_TO_EUR = 0.92


class AgentSerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True, allow_null=True)
    max_cost_eur = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        fields = [
            "id", "template_id", "workspace", "name", "agent_type", "description", "system_prompt",
            "max_steps", "max_cost_usd", "max_cost_eur", "llm_model", "tools", "is_active",
            "created_by", "created_by_email", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "template_id", "workspace", "created_by", "max_cost_eur", "created_at", "updated_at"]

    def get_max_cost_eur(self, obj):
        return round(float(obj.max_cost_usd or 0) * _USD_TO_EUR, 4)


class CreateAgentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Agent
        fields = ["name", "agent_type", "description", "system_prompt",
                  "max_steps", "max_cost_usd", "llm_model", "tools"]
