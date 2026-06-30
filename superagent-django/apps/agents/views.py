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


# ---------------------------------------------------------------------------
# Tool display registry — maps tool_name → (label, icon, risk_level)
# ---------------------------------------------------------------------------
_TOOL_DISPLAY = {
    "read_email":     ("Gmail Read",    "mail",         "safe"),
    "send_email":     ("Gmail Send",    "send",         "high"),
    "create_draft":   ("Create Draft",  "edit-3",       "safe"),
    "classify_text":  ("Classify Text", "tag",          "safe"),
    "web_search":     ("Web Search",    "search",       "safe"),
    "file_read":      ("File Read",     "file",         "safe"),
    "file_write":     ("File Write",    "file-plus",    "medium"),
    "browse_web":     ("Browse Web",    "globe",        "safe"),
    "cal_read":       ("Calendar Read", "calendar",     "safe"),
    "cal_write":      ("Calendar Write","calendar",     "medium"),
    "delete_file":    ("Delete File",   "trash-2",      "high"),
    "export_csv":     ("Export CSV",    "download",     "safe"),
    "upload_to_drive":("Drive Upload",  "upload-cloud", "safe"),
    "generate_report":("Generate Report","file-text",   "safe"),
}
_TOOL_DEFAULT = ("Tool",        "zap",    "safe")

_RISK_COLOR = {"high": "#F59E0B", "medium": "#3B82F6", "safe": "#374151"}


def _tool_card(tool_name):
    label, icon, risk = _TOOL_DISPLAY.get(tool_name, _TOOL_DEFAULT)
    return {
        "name":       tool_name,
        "label":      label,
        "icon":       icon,
        "risk":       risk,
        "risk_color": _RISK_COLOR[risk],
        "needs_approval": risk == "high",
    }


def _human_ago(dt):
    from django.utils import timezone
    seconds = int((timezone.now() - dt).total_seconds())
    if seconds < 60:   return "just now"
    if seconds < 3600: return "%dm ago" % (seconds // 60)
    if seconds < 86400: return "%dh ago" % (seconds // 3600)
    return "%dd ago" % (seconds // 86400)


# ---------------------------------------------------------------------------
# Screen 1 — Agent Detail
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_mobile_detail(request, pk):
    """
    GET /api/v1/agents/{id}/mobile-detail/
    Agent Detail screen — header, status badges, stats, capabilities,
    tools chips, recent runs.
    """
    import datetime
    from django.utils import timezone
    from django.db.models import Count, Q
    from apps.tasks.models import Task

    workspace = _get_workspace(request)
    agent = get_object_or_404(Agent, id=pk, workspace=workspace)

    today = datetime.date.today()
    one_hour_ago = timezone.now() - datetime.timedelta(hours=1)

    desc, icon, icon_bg, border_color = _AGENT_DISPLAY.get(agent.agent_type, _DEFAULT_DISPLAY)

    # ── Stats ────────────────────────────────────────────────────────────────
    tasks_today = Task.objects.filter(agent=agent, created_at__date=today).count()

    total_tasks = Task.objects.filter(agent=agent).count()
    completed   = Task.objects.filter(agent=agent, status=Task.Status.COMPLETED).count()
    success_rate = round((completed / total_tasks * 100), 1) if total_tasks > 0 else 0.0

    from django.db.models import Sum
    total_cost = Task.objects.filter(agent=agent).aggregate(s=Sum("cost_usd"))["s"] or 0
    total_cost = float(total_cost)

    # ── Health / status ───────────────────────────────────────────────────────
    recent_fail  = Task.objects.filter(agent=agent, status=Task.Status.FAILED,
                                        completed_at__gte=one_hour_ago).exists()
    running_now  = Task.objects.filter(agent=agent, status=Task.Status.RUNNING).exists()
    waiting_approval = Task.objects.filter(
        agent=agent, status=Task.Status.WAITING_APPROVAL).exists()

    if running_now:
        health_label, health_color = "Running", "#F59E0B"
    elif recent_fail:
        health_label, health_color = "Error", "#EF4444"
    else:
        health_label, health_color = "Active", "#22C55E"

    # ── Capabilities (what this agent does) ──────────────────────────────────
    _CAPABILITIES = {
        "email": [
            "Reads all Gmail inbox emails",
            "Classifies urgent, invoice and spam",
            "Drafts replies — asks before sending",
            "Archives handled threads automatically",
        ],
        "finance": [
            "Reads and validates incoming invoices",
            "Flags duplicates and anomalies",
            "Categorises spend by vendor",
            "Exports to CSV for accounting",
        ],
        "document": [
            "Reads and summarises uploaded documents",
            "Extracts key data fields automatically",
            "Files documents into Drive folders",
            "Answers questions about document content",
        ],
        "research": [
            "Searches the web for relevant sources",
            "Summarises findings into a report",
            "Cites all sources used",
            "Flags conflicting information",
        ],
        "compliance": [
            "Monitors regulatory deadline calendars",
            "Sends Telegram alerts for urgent items",
            "Logs all compliance checks to audit trail",
            "Flags overdue items automatically",
        ],
        "reporting": [
            "Aggregates data from connected sources",
            "Generates PDF and markdown reports",
            "Schedules recurring report delivery",
            "Highlights anomalies and trends",
        ],
        "qa": [
            "Checks data quality across inputs",
            "Flags missing or inconsistent records",
            "Generates quality score per dataset",
            "Creates issues for failed checks",
        ],
        "custom": [
            "Executes your custom system prompt",
            "Uses the tools you have enabled",
            "Follows your configured guardrails",
            "Asks for approval on high-risk actions",
        ],
    }
    capabilities = _CAPABILITIES.get(agent.agent_type, _CAPABILITIES["custom"])

    # ── Tools ─────────────────────────────────────────────────────────────────
    tools = [_tool_card(t) for t in (agent.tools or [])]

    # ── Recent runs ───────────────────────────────────────────────────────────
    recent_tasks = (
        Task.objects
        .filter(agent=agent)
        .exclude(status__in=[Task.Status.QUEUED])
        .order_by("-created_at")[:5]
    )

    _STATUS_DOT = {
        Task.Status.COMPLETED:        "#22C55E",
        Task.Status.FAILED:           "#EF4444",
        Task.Status.RUNNING:          "#F59E0B",
        Task.Status.WAITING_APPROVAL: "#3B82F6",
        Task.Status.CANCELLED:        "#6B7280",
    }

    recent_runs = []
    for t in recent_tasks:
        duration = None
        if t.completed_at and t.started_at:
            secs = int((t.completed_at - t.started_at).total_seconds())
            if secs < 60:
                duration = "%ds" % secs
            elif secs < 3600:
                duration = "%dm" % (secs // 60)
            else:
                duration = "%dh" % (secs // 3600)

        recent_runs.append({
            "task_id":     str(t.id),
            "prompt":      t.prompt[:60],
            "status":      t.status,
            "dot_color":   _STATUS_DOT.get(t.status, "#6B7280"),
            "cost_usd":    round(float(t.cost_usd), 2),
            "cost_label":  "€%.2f" % float(t.cost_usd),
            "duration":    duration,
            "time_ago":    _human_ago(t.created_at),
            "created_at":  t.created_at.isoformat(),
        })

    return Response({
        "id":          str(agent.id),
        "name":        agent.name,
        "agent_type":  agent.agent_type,
        "description": agent.description or desc,
        "icon":        icon,
        "icon_bg":     icon_bg,
        "border_color": border_color,
        "is_active":   agent.is_active,
        "health_label": health_label,
        "health_color": health_color,
        "needs_approval_badge": waiting_approval,
        "stats": {
            "tasks_today":  tasks_today,
            "success_rate": success_rate,
            "success_rate_label": "%.1f%%" % success_rate,
            "total_cost_usd":  round(total_cost, 2),
            "total_cost_label": "€%.2f" % total_cost,
        },
        "capabilities": capabilities,
        "tools": tools,
        "recent_runs": recent_runs,
        "actions": {
            "live_log_url":  "/api/v1/agents/%s/live/" % pk,
            "audit_log_url": "/api/v1/agents/%s/audit-log/" % pk,
            "pause_url":     "/api/v1/agents/%s/update/" % pk,
            "can_pause":     agent.is_active,
        },
    })


# ---------------------------------------------------------------------------
# Screen 2 — Live Activity
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_live(request, pk):
    """
    GET /api/v1/agents/{id}/live/
    Live Activity screen — running task progress, queue, live log.
    """
    from django.utils import timezone
    from apps.tasks.models import Task, TaskStep

    workspace = _get_workspace(request)
    agent = get_object_or_404(Agent, id=pk, workspace=workspace)

    # ── Currently running task ────────────────────────────────────────────────
    running_task = (
        Task.objects.filter(agent=agent, status=Task.Status.RUNNING)
        .order_by("-started_at")
        .first()
    )

    running_now = None
    if running_task:
        steps_done = TaskStep.objects.filter(task=running_task).count()
        max_steps  = agent.max_steps or 20
        pct = min(100, round(steps_done / max_steps * 100))

        running_now = {
            "task_id":    str(running_task.id),
            "prompt":     running_task.prompt,
            "steps_done": steps_done,
            "max_steps":  max_steps,
            "progress_pct": pct,
            "progress_label": "Step %d of %d" % (steps_done, max_steps),
            "cost_so_far": round(float(running_task.cost_usd), 2),
            "cost_label":  "€%.2f" % float(running_task.cost_usd),
        }

    # ── Queue ─────────────────────────────────────────────────────────────────
    queued = (
        Task.objects.filter(agent=agent, status=Task.Status.QUEUED)
        .order_by("created_at")[:5]
    )

    _ORDINAL = ["next", "2nd", "3rd", "4th", "5th"]
    queue_items = [
        {
            "task_id": str(t.id),
            "prompt":  t.prompt[:50],
            "position_label": _ORDINAL[i] if i < len(_ORDINAL) else "%dth" % (i + 1),
        }
        for i, t in enumerate(queued)
    ]

    # ── Live log (most recent task steps, newest first) ───────────────────────
    if running_task:
        steps = TaskStep.objects.filter(task=running_task).order_by("-created_at")[:20]
    else:
        # Fallback: last completed task steps
        last_task = Task.objects.filter(agent=agent).exclude(
            status=Task.Status.QUEUED).order_by("-updated_at").first()
        steps = TaskStep.objects.filter(task=last_task).order_by("-created_at")[:20] if last_task else []

    _STEP_TAGS = {
        "started":  ("task.started",  "#3B82F6"),
        "thinking": ("thinking",      "#8B5CF6"),
        "tool":     ("tool.executed", "#22C55E"),
        "approval": ("approval.req",  "#F59E0B"),
        "error":    ("error",         "#EF4444"),
        "complete": ("task.done",     "#22C55E"),
    }

    live_log = []
    for step in steps:
        tag_key = "tool" if step.step_type == "tool_call" else (
                  "approval" if step.step_type == "approval_request" else
                  "started" if step.step_type == "start" else
                  "complete" if step.step_type == "complete" else "thinking")
        tag_label, tag_color = _STEP_TAGS.get(tag_key, ("event", "#6B7280"))

        live_log.append({
            "step_id":    str(step.id),
            "tag":        tag_label,
            "tag_color":  tag_color,
            "content":    step.content[:120] if step.content else "",
            "timestamp":  step.created_at.strftime("%H:%M:%S"),
            "created_at": step.created_at.isoformat(),
        })

    # ── Footer stats ──────────────────────────────────────────────────────────
    steps_done_count = len(live_log)
    cost_so_far      = running_now["cost_so_far"] if running_now else 0.0
    in_queue_count   = len(queue_items)
    is_live          = running_now is not None

    return Response({
        "agent_id":   str(agent.id),
        "agent_name": agent.name,
        "is_live":    is_live,
        "status_label": "Email Ops — running now" if is_live else agent.name + " — idle",
        "running_now": running_now,
        "queue":       queue_items,
        "live_log":    live_log,
        "footer": {
            "steps_done":   steps_done_count,
            "in_queue":     in_queue_count,
            "cost_so_far":  cost_so_far,
            "cost_label":   "€%.2f" % cost_so_far,
            "status":       "Live" if is_live else "Idle",
            "status_color": "#22C55E" if is_live else "#6B7280",
        },
    })


# ---------------------------------------------------------------------------
# Screen 3 — Agent Audit Log
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_audit_log(request, pk):
    """
    GET /api/v1/agents/{id}/audit-log/
    Audit Log screen for a single agent.

    Query params:
      ?filter=all|approvals|tools|errors   default: all
    """
    from apps.tasks.models import Task, TaskStep
    from apps.approvals.models import Approval

    workspace = _get_workspace(request)
    agent = get_object_or_404(Agent, id=pk, workspace=workspace)

    filter_by = request.query_params.get("filter", "all")

    # Collect events from task steps + approval records
    events = []

    # ── Approval events ───────────────────────────────────────────────────────
    if filter_by in ("all", "approvals"):
        approvals = (
            Approval.objects
            .filter(task__agent=agent)
            .select_related("task", "reviewer")
            .order_by("-created_at")[:30]
        )
        for ap in approvals:
            if ap.status == Approval.Status.PENDING:
                events.append({
                    "event_id":   str(ap.id),
                    "tag":        "approval.req",
                    "tag_color":  "#F59E0B",
                    "tag_bg":     "#78350F",
                    "title":      "%s → %s" % (ap.tool_name, ap.tool_input.get("to", "") if isinstance(ap.tool_input, dict) else ""),
                    "subtitle":   "Task: %s" % ap.task.prompt[:40],
                    "detail":     "Waited: —",
                    "cost_label": None,
                    "timestamp":  ap.created_at.strftime("%H:%M"),
                    "border_color": "#F59E0B",
                    "created_at": ap.created_at.isoformat(),
                })
            elif ap.status == Approval.Status.APPROVED:
                reviewer_name = ap.reviewer.name if ap.reviewer else "Someone"
                device = "iPhone"
                events.append({
                    "event_id":   str(ap.id) + "_granted",
                    "tag":        "approval.granted",
                    "tag_color":  "#22C55E",
                    "tag_bg":     "#064E3B",
                    "title":      "%s approved · %s" % (reviewer_name, device),
                    "subtitle":   "Task: %s" % ap.task.prompt[:40],
                    "detail":     None,
                    "cost_label": None,
                    "timestamp":  ap.reviewed_at.strftime("%H:%M") if ap.reviewed_at else "",
                    "border_color": "#22C55E",
                    "created_at": ap.reviewed_at.isoformat() if ap.reviewed_at else ap.created_at.isoformat(),
                })
            elif ap.status == Approval.Status.REJECTED:
                reviewer_name = ap.reviewer.name if ap.reviewer else "Someone"
                events.append({
                    "event_id":   str(ap.id) + "_rejected",
                    "tag":        "approval.rejected",
                    "tag_color":  "#EF4444",
                    "tag_bg":     "#7F1D1D",
                    "title":      "%s rejected" % reviewer_name,
                    "subtitle":   "Task: %s" % ap.task.prompt[:40],
                    "detail":     ap.reviewer_note or None,
                    "cost_label": None,
                    "timestamp":  ap.reviewed_at.strftime("%H:%M") if ap.reviewed_at else "",
                    "border_color": "#EF4444",
                    "created_at": ap.reviewed_at.isoformat() if ap.reviewed_at else ap.created_at.isoformat(),
                })

    # ── Tool executed events ──────────────────────────────────────────────────
    if filter_by in ("all", "tools"):
        tool_steps = (
            TaskStep.objects
            .filter(task__agent=agent, step_type="tool_call")
            .select_related("task")
            .order_by("-created_at")[:30]
        )
        for step in tool_steps:
            events.append({
                "event_id":   str(step.id),
                "tag":        "tool.executed",
                "tag_color":  "#3B82F6",
                "tag_bg":     "#1E3A5F",
                "title":      step.content[:80] if step.content else "Tool executed",
                "subtitle":   "Task: %s" % step.task.prompt[:40],
                "detail":     None,
                "cost_label": "€%.2f" % float(step.cost_usd) if hasattr(step, "cost_usd") else None,
                "timestamp":  step.created_at.strftime("%H:%M"),
                "border_color": "#3B82F6",
                "created_at": step.created_at.isoformat(),
            })

    # ── Error / failed task events ────────────────────────────────────────────
    if filter_by in ("all", "errors"):
        failed_tasks = (
            Task.objects
            .filter(agent=agent, status=Task.Status.FAILED)
            .order_by("-updated_at")[:10]
        )
        for t in failed_tasks:
            events.append({
                "event_id":   str(t.id) + "_fail",
                "tag":        "task.failed",
                "tag_color":  "#EF4444",
                "tag_bg":     "#7F1D1D",
                "title":      t.prompt[:60],
                "subtitle":   t.result[:60] if t.result else "Task failed",
                "detail":     None,
                "cost_label": None,
                "timestamp":  t.updated_at.strftime("%H:%M"),
                "border_color": "#EF4444",
                "created_at": t.updated_at.isoformat(),
            })

    # Sort newest first
    events.sort(key=lambda e: e["created_at"], reverse=True)

    return Response({
        "agent_id":    str(agent.id),
        "agent_name":  agent.name,
        "total_events": len(events),
        "subtitle":    "%d event%s" % (len(events), "s" if len(events) != 1 else ""),
        "active_filter": filter_by,
        "filters": [
            {"key": "all",       "label": "All"},
            {"key": "approvals", "label": "Approvals"},
            {"key": "tools",     "label": "Tools"},
            {"key": "errors",    "label": "Errors"},
        ],
        "events": events,
    })


# ---------------------------------------------------------------------------
# Screen 4 — Create Agent Form Config
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_create_form(request):
    """
    GET /api/v1/agents/create-form/
    Create Agent screen — returns all form options so the mobile app
    renders the form without hardcoding anything.
    """
    return Response({
        "identity": {
            "name_placeholder": "e.g. Executive Research Assistant",
            "agent_types": [
                {"value": "custom",      "label": "Custom",      "icon": "cpu"},
                {"value": "email",       "label": "Email",       "icon": "mail"},
                {"value": "calendar",    "label": "Calendar",    "icon": "calendar"},
                {"value": "research",    "label": "Research",    "icon": "search"},
                {"value": "finance",     "label": "Finance",     "icon": "wallet"},
                {"value": "document",    "label": "Document",    "icon": "file-text"},
                {"value": "reporting",   "label": "Reporting",   "icon": "bar-chart"},
                {"value": "compliance",  "label": "Compliance",  "icon": "shield"},
                {"value": "qa",          "label": "QA",          "icon": "check-square"},
            ],
        },
        "behaviour": {
            "description_placeholder": "What is this agent responsible for?",
            "system_prompt_placeholder": "You are a helpful assistant that specialises in...",
            "system_prompt_badge": "Core Logic",
        },
        "model": {
            "options": [
                {
                    "value":   "llama-3.1-8b-instant",
                    "label":   "Llama 3.1 8B",
                    "badge":   "Fast",
                    "badge_color": "#22C55E",
                    "is_default": True,
                },
                {
                    "value":   "llama-3.3-70b-versatile",
                    "label":   "Llama 3.3 70B",
                    "badge":   "Powerful",
                    "badge_color": "#3B82F6",
                    "is_default": False,
                },
                {
                    "value":   "mixtral-8x7b-32768",
                    "label":   "Mixtral 8x7B",
                    "badge":   "Long Context",
                    "badge_color": "#8B5CF6",
                    "is_default": False,
                },
            ],
            "default": "llama-3.1-8b-instant",
        },
        "tools": {
            "available": [
                {"name": "web_search",     "label": "Web Search",    "icon": "search",       "risk": "safe"},
                {"name": "file_read",      "label": "File Read",     "icon": "file",         "risk": "safe"},
                {"name": "file_write",     "label": "File Write",    "icon": "file-plus",    "risk": "medium"},
                {"name": "read_email",     "label": "Gmail Read",    "icon": "mail",         "risk": "safe"},
                {"name": "send_email",     "label": "Gmail Send",    "icon": "send",         "risk": "high"},
                {"name": "browse_web",     "label": "Browse Web",    "icon": "globe",        "risk": "safe"},
                {"name": "cal_read",       "label": "Cal Read",      "icon": "calendar",     "risk": "safe"},
                {"name": "cal_write",      "label": "Cal Write",     "icon": "calendar",     "risk": "medium"},
                {"name": "classify_text",  "label": "Classify Text", "icon": "tag",          "risk": "safe"},
                {"name": "create_draft",   "label": "Create Draft",  "icon": "edit-3",       "risk": "safe"},
                {"name": "export_csv",     "label": "Export CSV",    "icon": "download",     "risk": "safe"},
                {"name": "generate_report","label": "Gen Report",    "icon": "file-text",    "risk": "safe"},
            ],
            "risk_note": "Tools marked high will always require your approval before running.",
        },
        "guardrails": {
            "max_steps": {
                "label":    "Max Steps",
                "subtitle": "Iteration limit per request",
                "icon":     "repeat",
                "default":  19,
                "min":      1,
                "max":      50,
            },
            "max_cost_usd": {
                "label":    "Max Cost (USD)",
                "subtitle": "Maximum budget per run",
                "icon":     "dollar-sign",
                "default":  1.00,
                "min":      0.10,
                "max":      50.00,
                "step":     0.10,
            },
        },
        "submit_label": "Create Agent",
        "submit_url":   "/api/v1/agents/create/",
        "submit_method": "POST",
    })
