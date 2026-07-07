from rest_framework import serializers
from .models import Task, TaskStep

_USD_TO_EUR = 0.92


class TaskStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskStep
        fields = [
            "id", "step_number", "step_type",
            "agent_name", "title", "detail",
            "content", "tool_name", "tool_input", "tool_output", "tool_zone",
            "tokens_used", "created_at",
        ]


class TaskSerializer(serializers.ModelSerializer):
    steps = TaskStepSerializer(many=True, read_only=True)
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True)
    agent_name = serializers.CharField(source="agent.name", read_only=True, allow_null=True)
    cost_eur = serializers.SerializerMethodField()
    progress_percent = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id", "workspace", "agent", "agent_name", "created_by", "created_by_email",
            "prompt", "priority", "status", "result", "error_message",
            "steps_taken", "total_steps_estimate", "total_tokens", "cost_eur",
            "progress_percent", "deliverables",
            "started_at", "completed_at", "created_at", "updated_at",
            "steps",
        ]
        read_only_fields = [
            "id", "workspace", "created_by", "status", "result", "error_message",
            "steps_taken", "total_tokens",
            "started_at", "completed_at", "created_at", "updated_at",
            "celery_task_id",
        ]

    def get_cost_eur(self, obj):
        return round(float(obj.cost_usd or 0) * _USD_TO_EUR, 4)

    def get_progress_percent(self, obj):
        if obj.status == Task.Status.COMPLETED:
            return 100
        if obj.status in (Task.Status.FAILED, Task.Status.CANCELLED):
            return 0
        if obj.status == Task.Status.QUEUED:
            return 0
        # running or waiting_approval — estimate from steps taken
        estimate = obj.total_steps_estimate
        if not estimate and obj.agent:
            estimate = obj.agent.max_steps
        if not estimate:
            estimate = 20
        pct = round((obj.steps_taken / estimate) * 100)
        return min(pct, 95)  # cap at 95% — only 100 on completed


class CreateTaskSerializer(serializers.Serializer):
    prompt    = serializers.CharField(max_length=500)
    agent_id  = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    agent     = serializers.CharField(required=False, allow_null=True, allow_blank=True)  # alias for agent_id
    priority  = serializers.ChoiceField(choices=["routine", "urgent"], default="routine")


class TaskListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views — no steps."""
    agent_name = serializers.CharField(source="agent.name", read_only=True, allow_null=True)
    cost_eur = serializers.SerializerMethodField()
    progress_percent = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id", "agent", "agent_name", "prompt", "status",
            "steps_taken", "total_steps_estimate", "total_tokens", "cost_eur",
            "priority", "progress_percent", "deliverables",
            "started_at", "completed_at", "created_at",
        ]

    def get_cost_eur(self, obj):
        return round(float(obj.cost_usd or 0) * _USD_TO_EUR, 4)

    def get_progress_percent(self, obj):
        if obj.status == Task.Status.COMPLETED:
            return 100
        if obj.status in (Task.Status.FAILED, Task.Status.CANCELLED):
            return 0
        if obj.status == Task.Status.QUEUED:
            return 0
        estimate = obj.total_steps_estimate
        if not estimate and obj.agent:
            estimate = obj.agent.max_steps
        if not estimate:
            estimate = 20
        pct = round((obj.steps_taken / estimate) * 100)
        return min(pct, 95)
