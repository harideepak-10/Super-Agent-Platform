from rest_framework import serializers
from .models import AuditEvent


class AuditEventSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True, allow_null=True)

    class Meta:
        model = AuditEvent
        fields = [
            "id", "workspace", "actor", "actor_email", "event_type",
            "resource_type", "resource_id", "metadata",
            "ip_address", "user_agent", "created_at",
        ]
