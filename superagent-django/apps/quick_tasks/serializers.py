from rest_framework import serializers
from .models import QuickTask


class QuickTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model  = QuickTask
        fields = ["id", "title", "prompt", "agent_type", "icon", "source", "order"]
