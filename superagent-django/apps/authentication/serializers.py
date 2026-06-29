from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "name", "avatar_url", "created_at"]
        read_only_fields = ["id", "created_at"]


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])

    class Meta:
        model = User
        fields = ["email", "name", "password"]

    def create(self, validated_data):
        from django.utils.text import slugify
        import uuid
        from apps.authentication.models import Workspace
        from apps.team.models import TeamMembership

        user = User.objects.create_user(**validated_data)

        # Auto-create a workspace and make the user its owner
        base_slug = slugify(user.email.split("@")[0]) or "workspace"
        slug = base_slug
        # Ensure slug is unique
        if Workspace.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{uuid.uuid4().hex[:6]}"

        workspace = Workspace.objects.create(
            name=f"{user.name or user.email.split('@')[0]}'s Workspace",
            slug=slug,
            owner=user,
        )

        # Create membership so _get_workspace() can find it
        TeamMembership.objects.create(
            workspace=workspace,
            user=user,
            role=TeamMembership.Role.OWNER,
        )

        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class GoogleLoginSerializer(serializers.Serializer):
    id_token = serializers.CharField()


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(validators=[validate_password])


class UpdateProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["name", "avatar_url"]


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
