from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Agent
from .serializers import AgentSerializer, CreateAgentSerializer
from apps.audit.utils import log_event


def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_list(request):
    workspace = _get_workspace(request)
    agents = Agent.objects.filter(workspace=workspace, is_active=True)
    return Response(AgentSerializer(agents, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def agent_create(request):
    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace."}, status=status.HTTP_400_BAD_REQUEST)

    serializer = CreateAgentSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    agent = serializer.save(workspace=workspace, created_by=request.user)
    log_event(request, "agent_created", "agent", str(agent.id), workspace)
    return Response(AgentSerializer(agent).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_detail(request, pk):
    workspace = _get_workspace(request)
    agent = get_object_or_404(Agent, id=pk, workspace=workspace)
    return Response(AgentSerializer(agent).data)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def agent_update(request, pk):
    workspace = _get_workspace(request)
    agent = get_object_or_404(Agent, id=pk, workspace=workspace)
    serializer = CreateAgentSerializer(agent, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    log_event(request, "agent_updated", "agent", str(agent.id), workspace)
    return Response(AgentSerializer(agent).data)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def agent_delete(request, pk):
    workspace = _get_workspace(request)
    agent = get_object_or_404(Agent, id=pk, workspace=workspace)
    agent.is_active = False
    agent.save(update_fields=["is_active"])
    log_event(request, "agent_deleted", "agent", str(agent.id), workspace)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_tasks(request, pk):
    from apps.tasks.models import Task
    from apps.tasks.serializers import TaskListSerializer

    workspace = _get_workspace(request)
    agent = get_object_or_404(Agent, id=pk, workspace=workspace)
    tasks = Task.objects.filter(agent=agent).order_by("-created_at")
    return Response(TaskListSerializer(tasks, many=True).data)


# ---------------------------------------------------------------------------
# Mobile Agents screen
# ---------------------------------------------------------------------------

# Per-type display config: (description, icon, icon_bg_color, top_border_color)
_AGENT_DISPLAY = {
    "email":      ("Inbox triage & automated replies",           "mail",         "#B45309", "#F59E0B"),
    "calendar":   ("Schedule meetings & manage your calendar",   "calendar",     "#065F46", "#10B981"),
    "research":   ("Web research & information gathering",       "search",       "#1E40AF", "#3B82F6"),
    "document":   ("Extract, summarise & file documents",        "file-text",    "#0F766E", "#14B8A6"),
    "finance":    ("Invoice processing & expense reports",       "wallet",       "#166534", "#22C55E"),
    "reporting":  ("Generate reports & summaries",               "bar-chart",    "#5B21B6", "#8B5CF6"),
    "compliance": ("Deadline tracking & regulatory checks",      "shield",       "#92400E", "#F59E0B"),
    "qa":         ("Data quality checks & issue flagging",       "check-square", "#991B1B", "#EF4444"),
    "custom":     ("Your custom AI agent",                       "cpu",          "#1E3A5F", "#3B82F6"),
    "orchestrator":("Goal decomposition & task routing",         "git-branch",   "#1E3A5F", "#3B82F6"),
}
_DEFAULT_DISPLAY = ("AI agent", "cpu", "#1E3A5F", "#3B82F6")

# Available-to-hire marketplace agents (not yet in the workspace)
_MARKETPLACE = [
    {
        "type":        "calendar",
        "name":        "Calendar Agent",
        "description": "Schedule meetings & manage your calendar auto...",
        "icon":        "calendar",
        "icon_bg":     "#065F46",
        "price_eur":   0.10,
        "price_label": "€0.10/mo",
    },
    {
        "type":        "calculator",
        "name":        "Calculator Agent",
        "description": "Run calculations & formulas on demand",
        "icon":        "calculator",
        "icon_bg":     "#1E3A5F",
        "price_eur":   0.05,
        "price_label": "€0.05/mo",
    },
    {
        "type":        "spreadsheet",
        "name":        "Spreadsheet Agent",
        "description": "Analyze, edit & automate spreadsheets",
        "icon":        "table",
        "icon_bg":     "#4C1D95",
        "price_eur":   0.15,
        "price_label": "€0.15/mo",
    },
    {
        "type":        "research",
        "name":        "Research Agent",
        "description": "Deep web research on any topic",
        "icon":        "search",
        "icon_bg":     "#1E40AF",
        "price_eur":   0.12,
        "price_label": "€0.12/mo",
    },
]


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_mobile_list(request):
    """
    GET /api/v1/agents/mobile/

    Agents screen — active agent grid cards + marketplace section.

    Each agent card returns:
      name, description, icon, icon_bg_color, top_border_color,
      status_dot (green/amber/red), tasks_today, is_healthy

    Marketplace section returns agents not yet hired (filtered by
    existing agent_types in the workspace).
    """
    from datetime import date
    from django.utils import timezone
    from apps.tasks.models import Task

    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace."}, status=status.HTTP_400_BAD_REQUEST)

    active_agents = Agent.objects.filter(workspace=workspace, is_active=True)
    today = date.today()
    one_hour_ago = timezone.now() - __import__("datetime").timedelta(hours=1)

    total_tasks_today = Task.objects.filter(
        workspace=workspace,
        created_at__date=today,
    ).count()

    agent_cards = []
    for agent in active_agents:
        desc, icon, icon_bg, border_color = _AGENT_DISPLAY.get(agent.agent_type, _DEFAULT_DISPLAY)

        # Use agent's own description if set, else fallback to type default
        display_desc = agent.description or desc

        tasks_today = Task.objects.filter(
            agent=agent, created_at__date=today
        ).count()

        # Health: red dot if any task failed in the last hour
        recent_fail = Task.objects.filter(
            agent=agent,
            status=Task.Status.FAILED,
            completed_at__gte=one_hour_ago,
        ).exists()

        running_now = Task.objects.filter(
            agent=agent, status=Task.Status.RUNNING
        ).exists()

        if recent_fail:
            dot_color = "#EF4444"   # red
            health = "error"
        elif running_now:
            dot_color = "#F59E0B"   # amber — busy
            health = "busy"
        else:
            dot_color = "#22C55E"   # green
            health = "healthy"

        agent_cards.append({
            "id":           str(agent.id),
            "name":         agent.name,
            "agent_type":   agent.agent_type,
            "description":  display_desc,
            "icon":         icon,
            "icon_bg_color":   icon_bg,
            "top_border_color": border_color,
            "dot_color":    dot_color,
            "health":       health,
            "is_active":    agent.is_active,
            "tasks_today":  tasks_today,
            "tasks_label":  "%d task%s" % (tasks_today, "s" if tasks_today != 1 else ""),
            "tools_count":  len(agent.tools) if agent.tools else 0,
        })

    # Marketplace: exclude types already in workspace
    existing_types = set(active_agents.values_list("agent_type", flat=True))
    marketplace = [m for m in _MARKETPLACE if m["type"] not in existing_types]

    return Response({
        "total_agents":     len(agent_cards),
        "total_tasks_today": total_tasks_today,
        "header_subtitle":  "%d agent%s · %d tasks today" % (
            len(agent_cards), "s" if len(agent_cards) != 1 else "", total_tasks_today
        ),
        "agents":      agent_cards,
        "marketplace": marketplace,
    })
