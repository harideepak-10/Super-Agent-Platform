from rest_framework import serializers
from .models import Agent


class AgentSerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True, allow_null=True)

    class Meta:
        model = Agent
        fields = [
            "id", "workspace", "name", "agent_type", "description", "system_prompt",
            "max_steps", "max_cost_usd", "llm_model", "tools", "is_active",
            "created_by", "created_by_email", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "workspace", "created_by", "created_at", "updated_at"]


class CreateAgentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Agent
        fields = ["name", "agent_type", "description", "system_prompt",
                  "max_steps", "max_cost_usd", "llm_model", "tools"]
