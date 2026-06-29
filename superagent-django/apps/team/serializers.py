from rest_framework import serializers
from .models import TeamMembership, TeamInvitation


class TeamMemberSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    name = serializers.CharField(source="user.name", read_only=True)
    avatar_url = serializers.URLField(source="user.avatar_url", read_only=True)

    class Meta:
        model = TeamMembership
        fields = ["id", "user", "email", "name", "avatar_url", "role", "joined_at"]
        read_only_fields = ["id", "user", "email", "name", "avatar_url", "joined_at"]


class InviteMemberSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=TeamMembership.Role.choices, default=TeamMembership.Role.MEMBER)


class TeamInvitationSerializer(serializers.ModelSerializer):
    invited_by_email = serializers.EmailField(source="invited_by.email", read_only=True)

    class Meta:
        model = TeamInvitation
        fields = ["id", "email", "role", "invited_by", "invited_by_email", "status", "expires_at", "created_at"]
        read_only_fields = ["id", "invited_by", "status", "expires_at", "created_at"]


class UpdateRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=TeamMembership.Role.choices)
