from rest_framework import serializers
from .models import Approval, ApprovalRule


class ApprovalSerializer(serializers.ModelSerializer):
    reviewer_email = serializers.EmailField(source="reviewer.email", read_only=True, allow_null=True)
    task_prompt = serializers.CharField(source="task.prompt", read_only=True)

    class Meta:
        model = Approval
        fields = [
            "id", "task", "task_prompt", "tool_name", "tool_input", "tool_zone",
            "status", "reviewer", "reviewer_email", "reviewer_note",
            "expires_at", "reviewed_at", "created_at",
        ]
        read_only_fields = ["id", "task", "tool_name", "tool_input", "tool_zone",
                            "reviewer", "reviewed_at", "created_at"]


class ApprovalDecisionSerializer(serializers.Serializer):
    approved = serializers.BooleanField()
    note = serializers.CharField(required=False, allow_blank=True, default="")


class ApprovalRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovalRule
        fields = ["id", "workspace", "tool_name", "always_require", "always_block", "created_at"]
        read_only_fields = ["id", "workspace", "created_at"]
