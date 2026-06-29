from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import CustomerProfile, CustomerInteraction
from .serializers import CustomerProfileSerializer, CustomerInteractionSerializer


def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def profile_list(request):
    workspace = _get_workspace(request)
    profiles = CustomerProfile.objects.filter(workspace=workspace)
    q = request.query_params.get("q")
    if q:
        profiles = profiles.filter(email__icontains=q) | profiles.filter(name__icontains=q)
    return Response(CustomerProfileSerializer(profiles, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def profile_create(request):
    workspace = _get_workspace(request)
    serializer = CustomerProfileSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    profile = serializer.save(workspace=workspace, created_by=request.user)
    return Response(CustomerProfileSerializer(profile).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def profile_detail(request, pk):
    workspace = _get_workspace(request)
    profile = get_object_or_404(CustomerProfile, id=pk, workspace=workspace)

    if request.method == "GET":
        return Response(CustomerProfileSerializer(profile).data)

    if request.method == "DELETE":
        profile.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = CustomerProfileSerializer(profile, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(CustomerProfileSerializer(profile).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def profile_by_email(request):
    workspace = _get_workspace(request)
    email = request.query_params.get("email", "").strip().lower()
    if not email:
        return Response({"detail": "email query param required."}, status=status.HTTP_400_BAD_REQUEST)
    profile = CustomerProfile.objects.filter(workspace=workspace, email=email).first()
    if not profile:
        return Response({"found": False, "email": email})
    return Response({"found": True, **CustomerProfileSerializer(profile).data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def profile_interactions(request, pk):
    workspace = _get_workspace(request)
    profile = get_object_or_404(CustomerProfile, id=pk, workspace=workspace)
    interactions = CustomerInteraction.objects.filter(customer=profile).order_by("-created_at")[:50]
    return Response(CustomerInteractionSerializer(interactions, many=True).data)
