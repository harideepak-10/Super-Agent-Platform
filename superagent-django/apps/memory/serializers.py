from rest_framework import serializers
from .models import CustomerProfile, CustomerInteraction


class CustomerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerProfile
        fields = [
            "id", "workspace", "email", "name", "company", "role",
            "preferred_language", "communication_style", "urgency_preference",
            "custom_instructions", "escalation_contacts", "interaction_summary",
            "common_topics", "agent_notes", "interaction_count",
            "last_interaction_at", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "workspace", "interaction_count",
                            "last_interaction_at", "created_at", "updated_at"]


class CustomerInteractionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerInteraction
        fields = ["id", "customer", "task", "interaction_type", "summary", "metadata", "created_at"]
        read_only_fields = ["id", "created_at"]
