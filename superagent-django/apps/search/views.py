from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def global_search(request):
    query = request.query_params.get("q", "").strip()
    if not query or len(query) < 2:
        return Response({"tasks": [], "agents": [], "audit": []})

    workspace = _get_workspace(request)

    from apps.tasks.models import Task
    from apps.agents.models import Agent
    from apps.audit.models import AuditEvent
    from apps.tasks.serializers import TaskListSerializer
    from apps.agents.serializers import AgentSerializer
    from apps.audit.serializers import AuditEventSerializer

    tasks = Task.objects.filter(workspace=workspace, prompt__icontains=query)[:10]
    agents = Agent.objects.filter(workspace=workspace, name__icontains=query, is_active=True)[:10]
    events = AuditEvent.objects.filter(workspace=workspace, event_type__icontains=query)[:10]

    return Response({
        "tasks": TaskListSerializer(tasks, many=True).data,
        "agents": AgentSerializer(agents, many=True).data,
        "audit": AuditEventSerializer(events, many=True).data,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def search_tasks(request):
    query = request.query_params.get("q", "").strip()
    workspace = _get_workspace(request)

    from apps.tasks.models import Task
    from apps.tasks.serializers import TaskListSerializer

    tasks = Task.objects.filter(workspace=workspace)
    if query:
        tasks = tasks.filter(prompt__icontains=query)

    status_filter = request.query_params.get("status")
    if status_filter:
        tasks = tasks.filter(status=status_filter)

    return Response(TaskListSerializer(tasks[:20], many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def search_agents(request):
    query = request.query_params.get("q", "").strip()
    workspace = _get_workspace(request)

    from apps.agents.models import Agent
    from apps.agents.serializers import AgentSerializer

    agents = Agent.objects.filter(workspace=workspace, is_active=True)
    if query:
        agents = agents.filter(name__icontains=query)

    return Response(AgentSerializer(agents[:20], many=True).data)
