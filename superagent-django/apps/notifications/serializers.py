from rest_framework import serializers
from .models import Notification, NotificationSettings


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id", "notification_type", "title", "body", "is_read",
            "resource_type", "resource_id", "metadata", "created_at", "read_at",
        ]
        read_only_fields = ["id", "created_at"]


class NotificationSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationSettings
        fields = [
            "id", "email_on_task_complete", "email_on_task_failed",
            "email_on_approval_needed", "email_on_budget_alert", "push_enabled",
        ]
        read_only_fields = ["id"]
