from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Task, TaskStep
from .serializers import TaskSerializer, TaskListSerializer, CreateTaskSerializer, TaskStepSerializer
from apps.audit.utils import log_event


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

    # Filtering
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
    agent_id = serializer.validated_data.get("agent_id")
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

    # Urgent tasks bypass the normal queue — use high Celery priority
    if priority == "urgent":
        run_agent_task.apply_async(args=[str(task.id)], priority=9)
    else:
        run_agent_task.delay(str(task.id))

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
        "cost_usd": str(task.cost_usd),
        "total_tokens": task.total_tokens,
        "steps_taken": task.steps_taken,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def task_retry(request, pk):
    from .tasks import run_agent_task

    workspace = _get_workspace(request)
    task = get_object_or_404(Task, id=pk, workspace=workspace)

    if task.status not in (Task.Status.FAILED, Task.Status.CANCELLED):
        return Response({"detail": "Only failed or cancelled tasks can be retried."}, status=status.HTTP_400_BAD_REQUEST)

    # Create a new task from the same prompt
    new_task = Task.objects.create(
        workspace=workspace,
        agent=task.agent,
        created_by=request.user,
        prompt=task.prompt,
        status=Task.Status.QUEUED,
    )
    run_agent_task.delay(str(new_task.id))
    return Response(TaskSerializer(new_task).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Mobile Tasks screen
# ---------------------------------------------------------------------------

def _duration_label(seconds):
    """38s | 1m 12s | 4 min | 2h 5m"""
    if seconds is None:
        return None
    s = int(seconds)
    if s < 60:
        return "%ds" % s
    if s < 3600:
        m, sec = divmod(s, 60)
        return ("%dm %ds" % (m, sec)) if sec else ("%dm" % m)
    h, rem = divmod(s, 3600)
    m = rem // 60
    return ("%dh %dm" % (h, m)) if m else ("%dh" % h)


def _time_ago(dt):
    from django.utils import timezone as tz
    if not dt:
        return None
    secs = int((tz.now() - dt).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return "%dm ago" % (secs // 60)
    if secs < 86400:
        return "%dh ago" % (secs // 3600)
    return "%dd ago" % (secs // 86400)


_STATUS_META = {
    "queued":           ("Queued",            "#6B7280", "queued"),
    "running":          ("Running",           "#3B82F6", "running"),
    "waiting_approval": ("Awaiting approval", "#F59E0B", "waiting"),
    "completed":        ("Done",              "#10B981", "done"),
    "failed":           ("Failed",            "#EF4444", "failed"),
    "cancelled":        ("Cancelled",         "#9CA3AF", "cancelled"),
}
_DEFAULT_STATUS = ("Unknown", "#6B7280", "unknown")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def task_mobile_list(request):
    """
    GET /api/v1/tasks/mobile/

    Mobile Tasks screen — rich per-card data including progress, duration,
    cost label, accent color, and inline approval CTA.

    Query params:
      ?status=running|completed|failed|waiting_approval
      ?agent_id=<uuid>
      ?limit=N  (default 30)
    """
    from django.utils import timezone as tz
    from apps.approvals.models import Approval

    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace."}, status=status.HTTP_400_BAD_REQUEST)

    qs = (
        Task.objects
        .filter(workspace=workspace)
        .select_related("agent")
        .prefetch_related("approvals")
        .order_by("-updated_at")
    )

    status_filter = request.query_params.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)

    agent_filter = request.query_params.get("agent_id")
    if agent_filter:
        qs = qs.filter(agent_id=agent_filter)

    try:
        limit = min(int(request.query_params.get("limit", 30)), 100)
    except ValueError:
        limit = 30

    qs = qs[:limit]

    running_count = Task.objects.filter(workspace=workspace, status=Task.Status.RUNNING).count()
    waiting_count = Task.objects.filter(workspace=workspace, status=Task.Status.WAITING_APPROVAL).count()

    items = []
    for task in qs:
        status_label, accent, badge_key = _STATUS_META.get(task.status, _DEFAULT_STATUS)

        # Duration: running tasks = time since created; finished = completed_at - created_at
        now = tz.now()
        if task.completed_at:
            dur_secs = (task.completed_at - task.created_at).total_seconds()
        else:
            dur_secs = (now - task.created_at).total_seconds()
        duration = _duration_label(dur_secs)

        # Progress estimate for running tasks (steps_taken / max_steps or heuristic)
        progress_pct = None
        current_step_desc = None
        if task.status == Task.Status.RUNNING:
            # Use steps_taken as a rough proxy; cap at 95 while still running
            progress_pct = min(95, task.steps_taken * 10) if task.steps_taken else 5
            current_step_desc = task.result[:60] if task.result else "Processing..."
        elif task.status == Task.Status.WAITING_APPROVAL:
            progress_pct = None
            current_step_desc = task.result[:60] if task.result else "Waiting for approval..."

        # Pending approval ID for the "Review & Approve" CTA
        pending_approval = None
        if task.status == Task.Status.WAITING_APPROVAL:
            ap = task.approvals.filter(status=Approval.Status.PENDING).first()
            if ap:
                pending_approval = {
                    "id":           str(ap.id),
                    "tool_name":    ap.tool_name,
                    "display_name": ap.tool_name.replace("_", " ").title(),
                }

        # Cost label: format as €0.12
        cost_label = "€%.2f" % float(task.cost_usd)

        # Meta line: "4 min · 5 steps · €0.12"
        meta_parts = [duration]
        if task.steps_taken:
            meta_parts.append("%d step%s" % (task.steps_taken, "s" if task.steps_taken != 1 else ""))
        meta_parts.append(cost_label)
        meta_line = " · ".join(meta_parts)

        items.append({
            "id":           str(task.id),
            "prompt":       task.prompt[:100],
            "status":       task.status,
            "status_label": status_label,
            "badge_key":    badge_key,
            "accent_color": accent,

            "agent": {
                "id":         str(task.agent.id) if task.agent else None,
                "name":       task.agent.name if task.agent else "Agent",
                "agent_type": task.agent.agent_type if task.agent else "custom",
            },

            "progress_pct":       progress_pct,
            "current_step_desc":  current_step_desc,

            "duration":           duration,
            "steps_taken":        task.steps_taken,
            "cost_eur":           round(float(task.cost_usd), 4),
            "cost_label":         cost_label,
            "meta_line":          meta_line,

            "pending_approval":   pending_approval,
            "error_message":      task.error_message[:100] if task.error_message else None,

            "completed_at":       task.completed_at.isoformat() if task.completed_at else None,
            "updated_at":         task.updated_at.isoformat(),
            "time_ago":           _time_ago(task.updated_at),
        })

    return Response({
        "running_count": running_count,
        "waiting_count": waiting_count,
        "total_shown":   len(items),
        "tasks":         items,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def new_task_form(request):
    """
    GET /api/v1/tasks/new-task-form/

    Returns all config needed to render the New Task / Launch screen:
      - agents      list for the "Assign to" dropdown
      - quick_start preset task templates
      - priority    options with labels and descriptions
      - form_meta   character limits, placeholder text
    """
    from apps.agents.models import Agent

    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace."}, status=status.HTTP_400_BAD_REQUEST)

    # ── Agents dropdown ───────────────────────────────────────────────────────
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

    # ── Quick-start templates ─────────────────────────────────────────────────
    quick_start = [
        {
            "id":       "weekly_report",
            "label":    "Draft weekly report",
            "prompt":   "Draft a weekly summary report of all tasks completed this week, including key outcomes and any issues.",
            "icon":     "file-text",
            "agent_id": "auto",
        },
        {
            "id":       "extract_invoices",
            "label":    "Extract invoice data",
            "prompt":   "Extract all invoice data from the latest documents in Google Drive and export to a CSV file.",
            "icon":     "file-spreadsheet",
            "agent_id": "auto",
        },
        {
            "id":       "organize_drive",
            "label":    "Organize Google Drive",
            "prompt":   "Organize all files in Google Drive into appropriate folders by type and date.",
            "icon":     "cloud",
            "agent_id": "auto",
        },
        {
            "id":       "reply_emails",
            "label":    "Reply to urgent emails",
            "prompt":   "Check my inbox for urgent emails and draft replies for any that have been waiting more than 24 hours.",
            "icon":     "mail",
            "agent_id": "auto",
        },
        {
            "id":       "check_compliance",
            "label":    "Check compliance deadlines",
            "prompt":   "Check all upcoming compliance deadlines and alert me to anything due within the next 7 days.",
            "icon":     "shield",
            "agent_id": "auto",
        },
        {
            "id":       "triage_inbox",
            "label":    "Triage overnight inbox",
            "prompt":   "Triage all emails received overnight, flag urgent items and summarize the rest.",
            "icon":     "inbox",
            "agent_id": "auto",
        },
    ]

    # ── Priority options ──────────────────────────────────────────────────────
    priority_options = [
        {
            "value":       "routine",
            "label":       "Routine",
            "icon":        "clock",
            "description": None,
            "is_default":  True,
        },
        {
            "value":       "urgent",
            "label":       "Urgent",
            "icon":        "zap",
            "description": "Urgent tasks bypass the queue for immediate execution.",
            "is_default":  False,
        },
    ]

    return Response({
        "form_meta": {
            "title":           "New Task",
            "subtitle":        "Tell your agents exactly what to do",
            "prompt_label":    "WHAT SHOULD THE AI DO?",
            "prompt_placeholder": (
                "e.g. Send a follow-up email to all leads who "
                "haven't replied in 3 days..."
            ),
            "prompt_max_length": 500,
            "assign_label":    "ASSIGN TO",
            "quick_start_label": "QUICK START",
            "priority_label":  "PRIORITY",
            "submit_label":    "Run Task",
        },
        "agents":          agent_options,
        "quick_start":     quick_start,
        "priority_options": priority_options,
    })
