import threading

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Task, TaskStep
from .serializers import TaskSerializer, TaskListSerializer, CreateTaskSerializer, TaskStepSerializer
from apps.audit.utils import log_event


def _run_in_thread(celery_task, *args):
    """Run a Celery task in a background thread (free tier — no separate worker needed)."""
    def _worker():
        from django.db import connection
        try:
            celery_task.apply(args=args)
        finally:
            connection.close()
    threading.Thread(target=_worker, daemon=True).start()


def _get_workspace(request):
    """Return the first workspace the user belongs to (MVP: one workspace per user)."""
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def task_list(request):
    workspace = _get_workspace(request)
    if not workspace:
        return Response([], status=status.HTTP_200_OK)
    tasks = Task.objects.filter(workspace=workspace).order_by("-created_at")

    task_status = request.query_params.get("status")
    if task_status:
        tasks = tasks.filter(status=task_status)

    serializer = TaskListSerializer(tasks, many=True)
    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def task_create(request):
    from apps.agents.models import Agent
    from .tasks import run_agent_task

    serializer = CreateTaskSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace found."}, status=status.HTTP_400_BAD_REQUEST)

    agent = None
    agent_id = serializer.validated_data.get("agent_id") or serializer.validated_data.get("agent")
    if agent_id:
        agent = get_object_or_404(Agent, id=agent_id, workspace=workspace)

    priority = serializer.validated_data.get("priority", "routine")
    task = Task.objects.create(
        workspace=workspace,
        agent=agent,
        created_by=request.user,
        prompt=serializer.validated_data["prompt"],
        priority=priority,
        status=Task.Status.QUEUED,
    )

    _run_in_thread(run_agent_task, str(task.id))

    log_event(request, "task_created", "task", str(task.id), workspace)
    return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def task_detail(request, pk):
    workspace = _get_workspace(request)
    task = get_object_or_404(Task, id=pk, workspace=workspace)
    return Response(TaskSerializer(task).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def task_cancel(request, pk):
    from superagent.celery import app as celery_app

    workspace = _get_workspace(request)
    task = get_object_or_404(Task, id=pk, workspace=workspace)

    if task.status in (Task.Status.COMPLETED, Task.Status.FAILED, Task.Status.CANCELLED):
        return Response({"detail": "Task already finished."}, status=status.HTTP_400_BAD_REQUEST)

    if task.celery_task_id:
        celery_app.control.revoke(task.celery_task_id, terminate=True)

    task.status = Task.Status.CANCELLED
    task.save(update_fields=["status"])
    log_event(request, "task_cancelled", "task", str(task.id), workspace)
    return Response({"detail": "Task cancelled."})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def task_steps(request, pk):
    workspace = _get_workspace(request)
    task = get_object_or_404(Task, id=pk, workspace=workspace)
    steps = TaskStep.objects.filter(task=task).order_by("step_number")
    return Response(TaskStepSerializer(steps, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def task_result(request, pk):
    workspace = _get_workspace(request)
    task = get_object_or_404(Task, id=pk, workspace=workspace)
    return Response({
        "id": str(task.id),
        "status": task.status,
        "result": task.result,
        "error_message": task.error_message,
        "cost_eur": round(float(task.cost_usd or 0) * 0.92, 4),
        "total_tokens": task.total_tokens,
        "steps_taken": task.steps_taken,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def task_pending_approvals(request):
    """
    GET /api/v1/tasks/pending-approvals/
    Returns all tasks currently waiting for user approval, with approval_id included.
    """
    workspace = _get_workspace(request)
    if not workspace:
        return Response([], status=status.HTTP_200_OK)
    tasks = Task.objects.filter(
        workspace=workspace,
        status=Task.Status.WAITING_APPROVAL,
    ).order_by("-created_at")
    serializer = TaskListSerializer(tasks, many=True)
    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def task_retry(request, pk):
    from .tasks import run_agent_task

    workspace = _get_workspace(request)
    task = get_object_or_404(Task, id=pk, workspace=workspace)

    if task.status not in (Task.Status.FAILED, Task.Status.CANCELLED):
        return Response({"detail": "Only failed or cancelled tasks can be retried."}, status=status.HTTP_400_BAD_REQUEST)

    new_task = Task.objects.create(
        workspace=workspace,
        agent=task.agent,
        created_by=request.user,
        prompt=task.prompt,
        status=Task.Status.QUEUED,
    )
    _run_in_thread(run_agent_task, str(new_task.id))
    return Response(TaskSerializer(new_task).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# New Task form config
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def new_task_form(request):
    """
    GET /api/v1/tasks/new-task-form/
    Returns all config needed to render the New Task screen.
    """
    from apps.agents.models import Agent

    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace."}, status=status.HTTP_400_BAD_REQUEST)

    agents = Agent.objects.filter(workspace=workspace, is_active=True).order_by("name")

    agent_options = [
        {
            "value":       "auto",
            "label":       "Auto (Orchestrator)",
            "description": "The Orchestrator will automatically select the best agents for your request.",
            "is_default":  True,
        }
    ]
    for ag in agents:
        agent_options.append({
            "value":       str(ag.id),
            "label":       ag.name,
            "description": ag.description or None,
            "agent_type":  ag.agent_type,
            "is_default":  False,
        })

    quick_start = [
        {"id": "weekly_report",    "label": "Draft weekly report",       "prompt": "Draft a weekly summary report of all tasks completed this week, including key outcomes and any issues.", "icon": "file-text",        "agent_id": "auto"},
        {"id": "extract_invoices", "label": "Extract invoice data",      "prompt": "Extract all invoice data from the latest documents in Google Drive and export to a CSV file.",           "icon": "file-spreadsheet", "agent_id": "auto"},
        {"id": "organize_drive",   "label": "Organize Google Drive",     "prompt": "Organize all files in Google Drive into appropriate folders by type and date.",                           "icon": "cloud",            "agent_id": "auto"},
        {"id": "reply_emails",     "label": "Reply to urgent emails",    "prompt": "Check my inbox for urgent emails and draft replies for any that have been waiting more than 24 hours.",  "icon": "mail",             "agent_id": "auto"},
        {"id": "check_compliance", "label": "Check compliance deadlines","prompt": "Check all upcoming compliance deadlines and alert me to anything due within the next 7 days.",            "icon": "shield",           "agent_id": "auto"},
        {"id": "triage_inbox",     "label": "Triage overnight inbox",    "prompt": "Triage all emails received overnight, flag urgent items and summarize the rest.",                         "icon": "inbox",            "agent_id": "auto"},
    ]

    priority_options = [
        {"value": "routine", "label": "Routine", "icon": "clock", "description": None,                                                          "is_default": True},
        {"value": "urgent",  "label": "Urgent",  "icon": "zap",   "description": "Urgent tasks bypass the queue for immediate execution.", "is_default": False},
    ]

    return Response({
        "form_meta": {
            "title":                "New Task",
            "subtitle":             "Tell your agents exactly what to do",
            "prompt_label":         "WHAT SHOULD THE AI DO?",
            "prompt_placeholder":   "e.g. Send a follow-up email to all leads who haven't replied in 3 days...",
            "prompt_max_length":    500,
            "assign_label":         "ASSIGN TO",
            "quick_start_label":    "QUICK START",
            "priority_label":       "PRIORITY",
            "submit_label":         "Run Task",
        },
        "agents":           agent_options,
        "quick_start":      quick_start,
        "priority_options": priority_options,
    })
