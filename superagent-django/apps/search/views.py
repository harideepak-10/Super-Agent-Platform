import re
from functools import reduce

from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


def _word_q(fields, query):
    """
    Split query into individual words and build a Q filter using whole-word
    regex matching so that "read" does NOT match "ready" or "already".

    Each word is matched with \b word boundaries (case-insensitive).
    ALL words must be present (AND logic) — each word can appear in any field.

    Example:
        query = "send email"
        → prompt contains whole word "send" AND prompt/result/etc contains whole word "email"
    """
    words = [w for w in query.split() if w]
    if not words:
        return Q()

    word_qs = []
    for word in words:
        # (^|[^a-zA-Z]) ensures whole-word match without using \b which means
        # "backspace" in PostgreSQL POSIX regex (not word boundary like Python).
        # "read" matches "Read my emails" but NOT "ready" or "already".
        pattern = r'(^|[^a-zA-Z0-9])' + re.escape(word) + r'([^a-zA-Z0-9]|$)'
        field_q = reduce(lambda a, b: a | b, [Q(**{f + "__iregex": pattern}) for f in fields])
        word_qs.append(field_q)

    # ALL words must match (AND across words, OR across fields per word)
    return reduce(lambda a, b: a & b, word_qs)


# ─────────────────────────────────────────────────────────────────────────────
# Search Tasks — GET /api/v1/search/tasks/?q=send email
# ─────────────────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def search_tasks(request):
    """
    GET /api/v1/search/tasks/?q=send email&status=failed

    Searches across: prompt, result, agent name, status.
    Words are matched individually — "send email" matches any task
    containing "send" OR "email" in any of those fields.

    Optional filters:
      status   — completed | failed | running | waiting_approval
    """
    from apps.tasks.models import Task
    from apps.tasks.serializers import TaskListSerializer

    query = request.query_params.get("q", "").strip()
    workspace = _get_workspace(request)

    qs = Task.objects.filter(workspace=workspace).select_related("agent")

    if query:
        qs = qs.filter(
            _word_q(["prompt", "result", "agent__name", "status"], query)
        )

    status_filter = request.query_params.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)

    qs = qs.order_by("-created_at")[:20]
    return Response({
        "query": query,
        "count": qs.count() if not query else len(qs),
        "results": TaskListSerializer(qs, many=True).data,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Search Agents — GET /api/v1/search/agents/?q=email agent
# ─────────────────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def search_agents(request):
    """
    GET /api/v1/search/agents/?q=email agent

    Searches across: name, agent_type, description.
    "email agent" matches any agent whose name, type, or description
    contains "email" OR "agent".
    """
    from apps.agents.models import Agent
    from apps.agents.serializers import AgentSerializer

    query = request.query_params.get("q", "").strip()
    workspace = _get_workspace(request)

    qs = Agent.objects.filter(workspace=workspace, is_active=True)

    if query:
        qs = qs.filter(
            _word_q(["name", "agent_type", "description"], query)
        )

    qs = qs.order_by("name")[:20]
    return Response({
        "query": query,
        "count": len(qs),
        "results": AgentSerializer(qs, many=True).data,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Global Search — GET /api/v1/search/?q=email
# ─────────────────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def global_search(request):
    """
    GET /api/v1/search/?q=email

    Searches tasks, agents, and audit events simultaneously.
    Returns grouped results under tasks / agents / audit keys.
    Each group has a count and results list.
    """
    from apps.tasks.models import Task
    from apps.agents.models import Agent
    from apps.audit.models import AuditEvent
    from apps.tasks.serializers import TaskListSerializer
    from apps.agents.serializers import AgentSerializer
    from apps.audit.serializers import AuditEventSerializer

    query = request.query_params.get("q", "").strip()
    workspace = _get_workspace(request)

    if not query or len(query) < 2:
        return Response({
            "query": query,
            "tasks":  {"count": 0, "results": []},
            "agents": {"count": 0, "results": []},
            "audit":  {"count": 0, "results": []},
        })

    # Tasks — search prompt, result, agent name
    task_qs = Task.objects.filter(
        workspace=workspace,
    ).filter(
        _word_q(["prompt", "result", "agent__name"], query)
    ).select_related("agent").order_by("-created_at")[:10]

    # Agents — search name, type, description
    agent_qs = Agent.objects.filter(
        workspace=workspace, is_active=True,
    ).filter(
        _word_q(["name", "agent_type", "description"], query)
    ).order_by("name")[:10]

    # Audit — search event type and details
    audit_qs = AuditEvent.objects.filter(
        workspace=workspace,
    ).filter(
        _word_q(["event_type", "resource_type"], query)
    ).order_by("-created_at")[:10]

    return Response({
        "query": query,
        "tasks":  {"count": len(task_qs),  "results": TaskListSerializer(task_qs, many=True).data},
        "agents": {"count": len(agent_qs), "results": AgentSerializer(agent_qs, many=True).data},
        "audit":  {"count": len(audit_qs), "results": AuditEventSerializer(audit_qs, many=True).data},
    })
