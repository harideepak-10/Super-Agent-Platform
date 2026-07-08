"""
Quick Tasks — simple read-only list for the "Quick Start" section on the home screen.

GET  /api/v1/quick-tasks/           — returns max 4 quick tasks (seeds defaults on first use)
POST /api/v1/quick-tasks/<id>/remove/ — remove one (optional, so user can clear auto-added ones)

Auto-promotion:
  try_auto_promote(user, workspace, prompt, agent_type)
  Called from tasks/views.py after every task creation.
  If the same prompt has been submitted 3+ times, it's added to the list automatically.
"""

import re
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import QuickTask
from .serializers import QuickTaskSerializer

MAX_QUICK_TASKS    = 4
AUTO_PROMOTE_AFTER = 3   # runs before a prompt is auto-added

# ---------------------------------------------------------------------------
# Default quick tasks — seeded once per user on first GET
# ---------------------------------------------------------------------------
_DEFAULTS = [
    {
        "title":      "Draft daily report",
        "prompt":     "Draft a daily status report for today summarising what was completed, what is in progress, and what is blocked.",
        "agent_type": "document",
        "icon":       "file-text",
        "order":      0,
    },
    {
        "title":      "Extract invoice data",
        "prompt":     "Find all emails with invoices or billing attachments in my inbox, extract the key data (vendor, amount, due date), and create a summary.",
        "agent_type": "email",
        "icon":       "receipt",
        "order":      1,
    },
    {
        "title":      "Organize Google Drive",
        "prompt":     "List all files in my Google Drive, identify duplicates or unorganised files, and suggest a folder structure.",
        "agent_type": "document",
        "icon":       "folder",
        "order":      2,
    },
    {
        "title":      "Reply to urgent emails",
        "prompt":     "Find all urgent or high-priority unread emails in my inbox and draft professional replies for each one.",
        "agent_type": "email",
        "icon":       "mail",
        "order":      3,
    },
]


def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


def _seed_defaults(user, workspace):
    for item in _DEFAULTS:
        QuickTask.objects.get_or_create(
            workspace=workspace,
            user=user,
            title=item["title"],
            defaults={
                "prompt":     item["prompt"],
                "agent_type": item["agent_type"],
                "icon":       item["icon"],
                "order":      item["order"],
                "source":     QuickTask.Source.DEFAULT,
            },
        )


def _normalize(prompt: str) -> str:
    return re.sub(r"\s+", " ", prompt.strip().lower())


# ---------------------------------------------------------------------------
# Public helper — called from apps/tasks/views.py after every task creation
# ---------------------------------------------------------------------------
def try_auto_promote(user, workspace, prompt: str, agent_type: str = ""):
    """Auto-add prompt to quick tasks if user has submitted it 3+ times."""
    from apps.tasks.models import Task

    norm = _normalize(prompt)

    # Count how many times this prompt has been submitted
    all_prompts = Task.objects.filter(
        workspace=workspace, created_by=user
    ).values_list("prompt", flat=True)

    run_count = sum(1 for p in all_prompts if _normalize(p) == norm)
    if run_count < AUTO_PROMOTE_AFTER:
        return

    # Already in the list?
    existing_prompts = QuickTask.objects.filter(
        workspace=workspace, user=user
    ).values_list("prompt", flat=True)
    if any(_normalize(p) == norm for p in existing_prompts):
        return

    # Full?
    current_count = QuickTask.objects.filter(workspace=workspace, user=user).count()
    if current_count >= MAX_QUICK_TASKS:
        return

    # Build short title from the first 6 words
    words = prompt.strip().split()
    title = " ".join(words[:6]).rstrip(".,;:")
    if len(words) > 6:
        title += "…"

    QuickTask.objects.create(
        workspace=workspace,
        user=user,
        title=title,
        prompt=prompt,
        agent_type=agent_type,
        icon="zap",
        source=QuickTask.Source.AUTO,
        order=current_count,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def quick_task_list(request):
    """
    GET /api/v1/quick-tasks/

    Returns up to 4 quick tasks for the Quick Start section.
    Seeds the 4 defaults the first time a user calls this.
    """
    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace."}, status=status.HTTP_400_BAD_REQUEST)

    qs = QuickTask.objects.filter(workspace=workspace, user=request.user)

    if not qs.exists():
        _seed_defaults(request.user, workspace)
        qs = QuickTask.objects.filter(workspace=workspace, user=request.user)

    items = qs[:MAX_QUICK_TASKS]
    return Response(QuickTaskSerializer(items, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def quick_task_remove(request, pk):
    """
    POST /api/v1/quick-tasks/<id>/remove/

    Remove a quick task (e.g. if user doesn't want an auto-promoted one).
    """
    workspace = _get_workspace(request)
    try:
        qt = QuickTask.objects.get(id=pk, workspace=workspace, user=request.user)
    except QuickTask.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    qt.delete()
    return Response({"detail": "Removed."}, status=status.HTTP_200_OK)
