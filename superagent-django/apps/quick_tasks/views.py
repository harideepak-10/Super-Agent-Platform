"""
Quick Tasks — dynamic top-4 ranked list for the Quick Start section.

GET  /api/v1/quick-tasks/              — returns top 4 most-used prompts (recalculated on every call)
POST /api/v1/quick-tasks/remove/       — hide a prompt so it never appears in the list

How ranking works:
  1. Count how many times the user has submitted each unique prompt (from Task history)
  2. Sort by count descending → take top 4
  3. If fewer than 4 results, fill remaining slots with default quick tasks
  4. Dismissed prompts (via remove endpoint) are excluded permanently

If user has no task history at all → return the 4 default quick tasks.
"""

from __future__ import annotations
import re
import uuid
from collections import Counter

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import QuickTask  # used only to store dismissed prompts

MAX_QUICK_TASKS = 4

# ---------------------------------------------------------------------------
# Default quick tasks — shown when user has no history
# ---------------------------------------------------------------------------
_DEFAULTS = [
    {
        "title":      "Summarise unread emails",
        "prompt":     "Read all my unread emails and give me a clear summary of each one — subject, sender, and key message.",
        "agent_type": "email",
        "icon":       "mail",
    },
    {
        "title":      "Download latest attachment",
        "prompt":     "Find the most recent email that has an attachment and download it.",
        "agent_type": "email",
        "icon":       "paperclip",
    },
    {
        "title":      "Reply to an email",
        "prompt":     "Read my latest unread email, create a professional reply, and send it.",
        "agent_type": "email",
        "icon":       "send",
    },
    {
        "title":      "Summary of last 5 emails",
        "prompt":     "Read my last 5 emails and give me a summary of each — who sent it, what it's about, and if any action is needed.",
        "agent_type": "email",
        "icon":       "inbox",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


def _normalize(prompt: str) -> str:
    """Lowercase + collapse whitespace for comparison."""
    return re.sub(r"\s+", " ", prompt.strip().lower())


def _stable_id(prompt: str) -> str:
    """Generate a stable UUID from a prompt so frontend has a consistent ID."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, _normalize(prompt)))


def _detect_icon(prompt: str) -> str:
    lower = prompt.lower()
    if any(w in lower for w in ["email", "mail", "inbox", "reply", "send", "draft"]):
        return "mail"
    if any(w in lower for w in ["calendar", "meeting", "schedule", "event", "slot"]):
        return "calendar"
    if any(w in lower for w in ["invoice", "finance", "payment", "billing", "expense"]):
        return "receipt"
    if any(w in lower for w in ["drive", "folder", "file", "pdf", "document", "report", "presentation"]):
        return "file-text"
    if any(w in lower for w in ["translate"]):
        return "globe"
    if any(w in lower for w in ["search", "research", "find", "look up"]):
        return "search"
    return "zap"


def _detect_agent_type(prompt: str) -> str:
    lower = prompt.lower()
    if any(w in lower for w in ["email", "mail", "inbox", "reply", "send", "draft"]):
        return "email"
    if any(w in lower for w in ["calendar", "meeting", "schedule", "event", "slot"]):
        return "calendar"
    if any(w in lower for w in ["drive", "pdf", "document", "report", "invoice", "presentation", "translate"]):
        return "document"
    if any(w in lower for w in ["search", "research"]):
        return "research"
    return ""


def _build_item(prompt: str, run_count: int, source: str = "usage") -> dict:
    """Build a quick task dict from a prompt string."""
    words = prompt.strip().split()
    title = " ".join(words[:6]).rstrip(".,;:")
    if len(words) > 6:
        title += "…"
    return {
        "id":         _stable_id(prompt),
        "title":      title,
        "prompt":     prompt,
        "icon":       _detect_icon(prompt),
        "agent_type": _detect_agent_type(prompt),
        "source":     source,
        "run_count":  run_count,
    }


def _default_item(d: dict) -> dict:
    return {
        "id":         _stable_id(d["prompt"]),
        "title":      d["title"],
        "prompt":     d["prompt"],
        "icon":       d["icon"],
        "agent_type": d["agent_type"],
        "source":     "default",
        "run_count":  0,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def quick_task_list(request):
    """
    GET /api/v1/quick-tasks/

    Dynamically ranks the user's top 4 most-used prompts from task history.
    Fills remaining slots with defaults if fewer than 4 real prompts exist.
    Dismissed prompts are excluded.

    Recalculated fresh on every call — always reflects today's usage.
    """
    from apps.tasks.models import Task

    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace."}, status=status.HTTP_400_BAD_REQUEST)

    # Load dismissed prompt norms for this user
    dismissed_norms = set(
        _normalize(p)
        for p in QuickTask.objects.filter(
            workspace=workspace,
            user=request.user,
            source=QuickTask.Source.HIDDEN,
        ).values_list("prompt", flat=True)
    )

    # Count usage from Task history
    all_prompts = Task.objects.filter(
        workspace=workspace,
        created_by=request.user,
    ).values_list("prompt", flat=True)

    counter: Counter = Counter()
    # Keep the latest original (un-normalized) version of each prompt
    prompt_originals: dict[str, str] = {}

    for p in all_prompts:
        norm = _normalize(p)
        if norm in dismissed_norms:
            continue
        counter[norm] += 1
        prompt_originals[norm] = p   # last seen wins

    # Top 4 by frequency
    result = []
    for norm, count in counter.most_common(MAX_QUICK_TASKS):
        result.append(_build_item(prompt_originals[norm], count, source="usage"))

    # Fill remaining slots with defaults
    if len(result) < MAX_QUICK_TASKS:
        used_norms = {_normalize(r["prompt"]) for r in result}
        for d in _DEFAULTS:
            if len(result) >= MAX_QUICK_TASKS:
                break
            dn = _normalize(d["prompt"])
            if dn not in used_norms and dn not in dismissed_norms:
                result.append(_default_item(d))

    return Response(result)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def quick_task_remove(request):
    """
    POST /api/v1/quick-tasks/remove/

    Permanently hide a prompt from the quick task list.
    Body: { "prompt": "..." }

    The prompt will never appear in the list again even if it's the most used.
    """
    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace."}, status=status.HTTP_400_BAD_REQUEST)

    prompt = request.data.get("prompt", "").strip()
    if not prompt:
        return Response({"detail": "'prompt' is required."}, status=status.HTTP_400_BAD_REQUEST)

    # Store as hidden — get_or_create so no duplicates
    QuickTask.objects.get_or_create(
        workspace=workspace,
        user=request.user,
        title=prompt[:120],
        defaults={
            "prompt": prompt,
            "source": QuickTask.Source.HIDDEN,
        },
    )

    return Response({"detail": "Prompt hidden from quick tasks."})
