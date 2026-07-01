from rest_framework import serializers
from .models import Task, TaskStep


class TaskStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskStep
        fields = [
            "id", "step_number", "step_type", "content",
            "tool_name", "tool_input", "tool_output", "tool_zone",
            "tokens_used", "created_at",
        ]


class TaskSerializer(serializers.ModelSerializer):
    steps = TaskStepSerializer(many=True, read_only=True)
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True)
    agent_name = serializers.CharField(source="agent.name", read_only=True, allow_null=True)

    class Meta:
        model = Task
        fields = [
            "id", "workspace", "agent", "agent_name", "created_by", "created_by_email",
            "prompt", "status", "result", "error_message",
            "steps_taken", "total_tokens", "cost_usd",
            "started_at", "completed_at", "created_at", "updated_at",
            "steps",
        ]
        read_only_fields = [
            "id", "workspace", "created_by", "status", "result", "error_message",
            "steps_taken", "total_tokens", "cost_usd",
            "started_at", "completed_at", "created_at", "updated_at",
            "celery_task_id",
        ]


class CreateTaskSerializer(serializers.Serializer):
    prompt    = serializers.CharField(max_length=500)
    agent_id  = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    agent     = serializers.CharField(required=False, allow_null=True, allow_blank=True)  # alias for agent_id
    priority  = serializers.ChoiceField(choices=["routine", "urgent"], default="routine")


class TaskListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views — no steps."""
    agent_name = serializers.CharField(source="agent.name", read_only=True, allow_null=True)

    class Meta:
        model = Task
        fields = [
            "id", "agent", "agent_name", "prompt", "status",
            "steps_taken", "total_tokens", "cost_usd",
            "priority", "started_at", "completed_at", "created_at",
        ]
