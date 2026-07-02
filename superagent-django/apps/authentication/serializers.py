from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "name", "avatar_url", "created_at"]
        read_only_fields = ["id", "created_at"]

    def get_avatar_url(self, obj):
        request = self.context.get("request")
        if obj.avatar:
            url = obj.avatar.url
            return request.build_absolute_uri(url) if request else url
        return obj.avatar_url or None


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

        base_slug = slugify(user.email.split("@")[0]) or "workspace"
        slug = base_slug
        if Workspace.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{uuid.uuid4().hex[:6]}"

        workspace = Workspace.objects.create(
            name=f"{user.name or user.email.split('@')[0]}'s Workspace",
            slug=slug,
            owner=user,
        )

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
    avatar = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = User
        fields = ["name", "avatar_url", "avatar"]

    def update(self, instance, validated_data):
        avatar_file = validated_data.pop("avatar", None)
        if avatar_file:
            if instance.avatar:
                instance.avatar.delete(save=False)
            instance.avatar = avatar_file
            instance.avatar_url = ""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
