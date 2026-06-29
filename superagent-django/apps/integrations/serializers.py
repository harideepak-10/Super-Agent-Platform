from rest_framework import serializers
from .models import Integration


class IntegrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Integration
        fields = [
            "id", "workspace", "user", "provider", "status",
            "scopes", "metadata", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "workspace", "user", "created_at", "updated_at"]
        # Never expose tokens


class ConnectIntegrationSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=Integration.Provider.choices)
    auth_code = serializers.CharField(required=False)
    access_token = serializers.CharField(required=False)
    refresh_token = serializers.CharField(required=False)
    scopes = serializers.ListField(child=serializers.CharField(), required=False, default=list)
