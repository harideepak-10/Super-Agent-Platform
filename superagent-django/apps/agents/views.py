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
    return Response({"detail": "Agent deleted successfully."}, status=status.HTTP_200_OK)


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
# Agent display config — used by live, audit-log, and create-form
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
# Agent Overview — Detail screen (stats + capabilities + recent runs)
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_overview(request, pk):
    """
    GET /api/v1/agents/<id>/overview/

    Returns everything needed for the Agent Detail screen:
      - stats: tasks_today, success_rate, total_cost_eur
      - what_it_does: capability bullet points
      - tools: tool chips
      - recent_runs: last 3 completed tasks with time ago + cost
      - meta: name, type, description, template_id, is_active
    """
    from django.utils import timezone
    from django.db.models import Count, Sum, Q as DQ
    from apps.tasks.models import Task

    workspace = _get_workspace(request)
    agent = get_object_or_404(Agent, id=pk, workspace=workspace)

    today = timezone.now().date()

    # ── Stats ─────────────────────────────────────────────────────────────────
    tasks_today = Task.objects.filter(agent=agent, created_at__date=today).count()

    finished = Task.objects.filter(
        agent=agent,
        status__in=[Task.Status.COMPLETED, Task.Status.FAILED]
    ).aggregate(
        total=Count("id"),
        completed=Count("id", filter=DQ(status=Task.Status.COMPLETED)),
        total_cost=Sum("cost_usd"),
    )
    total_finished = finished["total"] or 0
    completed_count = finished["completed"] or 0
    success_rate = round(completed_count / total_finished * 100, 1) if total_finished else 0.0
    total_cost_eur = round(float(finished["total_cost"] or 0) * 0.92, 2)

    # ── Capabilities (from template or fallback to description) ───────────────
    template = _TEMPLATE_ID_MAP.get(agent.template_id) if agent.template_id else None
    capabilities = template["capabilities"] if template else [agent.description] if agent.description else []

    # ── Recent runs ───────────────────────────────────────────────────────────
    recent_tasks = (
        Task.objects.filter(agent=agent)
        .exclude(status__in=[Task.Status.QUEUED, Task.Status.RUNNING])
        .order_by("-updated_at")[:3]
    )

    STATUS_COLOR = {
        Task.Status.COMPLETED:        "#22C55E",
        Task.Status.FAILED:           "#EF4444",
        Task.Status.CANCELLED:        "#6B7280",
        Task.Status.WAITING_APPROVAL: "#F59E0B",
    }

    recent_runs = []
    for t in recent_tasks:
        age_seconds = int((timezone.now() - t.updated_at).total_seconds())
        if age_seconds < 3600:
            time_ago = "%dm" % (age_seconds // 60 or 1)
        elif age_seconds < 86400:
            time_ago = "%dh" % (age_seconds // 3600)
        else:
            time_ago = "%dd" % (age_seconds // 86400)

        recent_runs.append({
            "task_id":    str(t.id),
            "prompt":     t.prompt[:60],
            "status":     t.status,
            "status_color": STATUS_COLOR.get(t.status, "#6B7280"),
            "time_ago":   time_ago,
            "cost_eur":   round(float(t.cost_usd or 0) * 0.92, 4),
            "cost_label": "€%.2f" % (float(t.cost_usd or 0) * 0.92),
        })

    return Response({
        "id":          str(agent.id),
        "template_id": agent.template_id,
        "name":        agent.name,
        "agent_type":  agent.agent_type,
        "description": agent.description,
        "is_active":   agent.is_active,

        "stats": {
            "tasks_today":    tasks_today,
            "success_rate":   success_rate,
            "success_label":  "%.1f%%" % success_rate,
            "total_cost_eur": total_cost_eur,
            "cost_label":     "€%.2f" % total_cost_eur,
        },

        "what_it_does": capabilities,

        "tools": [_tool_card(tn) for tn in (agent.tools or [])],

        "recent_runs": recent_runs,

        "actions": {
            "live_log_url":   "/api/v1/agents/%s/live/" % agent.id,
            "audit_log_url":  "/api/v1/agents/%s/audit-log/" % agent.id,
            "all_tasks_url":  "/api/v1/agents/%s/tasks/" % agent.id,
        },
    })


# ---------------------------------------------------------------------------
# Live Activity
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
# Agent Templates — ready-made agents users can add with one tap
# ---------------------------------------------------------------------------

_AGENT_TEMPLATES = [
    {
        "id":          1,
        "slug":        "email-agent",
        "name":        "Email Agent",
        "agent_type":  "email",
        "description": "Reads your inbox, classifies emails, and sends replies. Requires Gmail connected.",
        "icon":        "mail",
        "icon_bg":     "#B45309",
        "border_color":"#F59E0B",
        "badge":       "Popular",
        "badge_color": "#22C55E",
        "capabilities": [
            "Reads and organises your inbox",
            "Classifies emails by priority and type",
            "Sends replies and automated responses",
            "Creates email drafts for review",
        ],
        "tools":       ["read_email", "classify_text", "send_email", "create_draft"],
        "llm_model":   "llama-3.3-70b-versatile",
        "system_prompt": (
            "You are an email management agent. You can read emails, classify them, "
            "send replies, and create drafts. When asked to send an email, always call "
            "the send_email tool directly with to, subject, and body fields. "
            "Never say you cannot send emails — use the tool."
        ),
        "max_steps":   20,
        "max_cost_usd": 1.0,
    },
    {
        "id":          2,
        "slug":        "research-agent",
        "name":        "Research Agent",
        "agent_type":  "research",
        "description": "Searches the web, browses pages, and generates structured research reports.",
        "icon":        "search",
        "icon_bg":     "#1E40AF",
        "border_color":"#3B82F6",
        "badge":       None,
        "badge_color": None,
        "capabilities": [
            "Searches the web for up-to-date information",
            "Browses and reads relevant web pages",
            "Generates structured research reports",
            "Summarises findings into key insights",
        ],
        "tools":       ["web_search", "browse_web", "generate_report"],
        "llm_model":   "llama-3.3-70b-versatile",
        "system_prompt": (
            "You are a research agent. Search the web for information, browse relevant pages, "
            "and generate clear structured reports. Always use tools — never make up information."
        ),
        "max_steps":   20,
        "max_cost_usd": 1.0,
    },
    {
        "id":          3,
        "slug":        "document-agent",
        "name":        "Document Agent",
        "agent_type":  "document",
        "description": "Reads files, extracts information, and exports summaries as CSV or reports.",
        "icon":        "file-text",
        "icon_bg":     "#0F766E",
        "border_color":"#14B8A6",
        "badge":       None,
        "badge_color": None,
        "capabilities": [
            "Parses PDF and DOCX documents",
            "Extracts structured data and tables",
            "Summarises content into key points",
            "Exports data as CSV or structured reports",
        ],
        "tools":       ["file_read", "generate_report", "export_csv"],
        "llm_model":   "llama-3.3-70b-versatile",
        "system_prompt": (
            "You are a document processing agent. Read files, extract key information, "
            "and generate structured summaries or CSV exports as needed."
        ),
        "max_steps":   15,
        "max_cost_usd": 1.0,
    },
    {
        "id":          4,
        "slug":        "calendar-agent",
        "name":        "Calendar Agent",
        "agent_type":  "calendar",
        "description": "Reads your calendar and schedules meetings. Requires Google Calendar connected.",
        "icon":        "calendar",
        "icon_bg":     "#065F46",
        "border_color":"#10B981",
        "badge":       None,
        "badge_color": None,
        "capabilities": [
            "Reads upcoming events and meetings",
            "Schedules new meetings on your calendar",
            "Checks availability and avoids conflicts",
            "Sends meeting invites and confirmations",
        ],
        "tools":       ["cal_read", "cal_write", "web_search"],
        "llm_model":   "llama-3.3-70b-versatile",
        "system_prompt": (
            "You are a calendar management agent. Read calendar events and schedule meetings "
            "when asked. Always confirm before creating or modifying events."
        ),
        "max_steps":   15,
        "max_cost_usd": 1.0,
    },
    {
        "id":          5,
        "slug":        "reporting-agent",
        "name":        "Reporting Agent",
        "agent_type":  "reporting",
        "description": "Generates business reports, summaries, and CSV exports from your data.",
        "icon":        "bar-chart",
        "icon_bg":     "#5B21B6",
        "border_color":"#8B5CF6",
        "badge":       None,
        "badge_color": None,
        "capabilities": [
            "Generates business performance reports",
            "Summarises data into actionable insights",
            "Exports reports as CSV or structured files",
            "Searches the web for benchmark comparisons",
        ],
        "tools":       ["generate_report", "export_csv", "web_search"],
        "llm_model":   "llama-3.3-70b-versatile",
        "system_prompt": (
            "You are a reporting agent. Generate structured business reports and export data "
            "as CSV when needed. Always use the generate_report tool for report creation."
        ),
        "max_steps":   15,
        "max_cost_usd": 1.0,
    },
]

_TEMPLATE_MAP  = {t["slug"]: t for t in _AGENT_TEMPLATES}
_TEMPLATE_ID_MAP = {t["id"]: t for t in _AGENT_TEMPLATES}


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_templates(request):
    """
    GET /api/v1/agents/templates/
    Returns all ready-made agent templates.
    """
    workspace = _get_workspace(request)
    existing_slugs = set()
    if workspace:
        existing_slugs = set(
            Agent.objects.filter(workspace=workspace, is_active=True)
            .values_list("agent_type", flat=True)
        )

    result = []
    for t in _AGENT_TEMPLATES:
        already_added = t["agent_type"] in existing_slugs
        result.append({
            "id":            t["id"],
            "slug":          t["slug"],
            "name":          t["name"],
            "agent_type":    t["agent_type"],
            "description":   t["description"],
            "icon":          t["icon"],
            "icon_bg":       t["icon_bg"],
            "border_color":  t["border_color"],
            "badge":         t["badge"],
            "badge_color":   t["badge_color"],
            "tools":         [_tool_card(tn) for tn in t["tools"]],
            "llm_model":     t["llm_model"],
            "already_added": already_added,
        })
    return Response(result)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_template_detail(request, template_id):
    """
    GET /api/v1/agents/templates/<id>/
    Full detail of a ready-made template before activating it.
    """
    workspace = _get_workspace(request)
    template = _TEMPLATE_ID_MAP.get(template_id)
    if not template:
        return Response({"detail": "Template not found."}, status=status.HTTP_404_NOT_FOUND)

    already_added = False
    activated_agent_id = None
    if workspace:
        existing = Agent.objects.filter(
            workspace=workspace, agent_type=template["agent_type"], is_active=True
        ).first()
        if existing:
            already_added = True
            activated_agent_id = str(existing.id)

    return Response({
        "id":            template["id"],
        "slug":          template["slug"],
        "name":          template["name"],
        "agent_type":    template["agent_type"],
        "description":   template["description"],
        "icon":          template["icon"],
        "icon_bg":       template["icon_bg"],
        "border_color":  template["border_color"],
        "badge":         template["badge"],
        "badge_color":   template["badge_color"],
        "what_it_does":  template["capabilities"],
        "tools":         [_tool_card(tn) for tn in template["tools"]],
        "llm_model":     template["llm_model"],
        "max_steps":     template["max_steps"],
        "max_cost_eur":  round(template["max_cost_usd"] * 0.92, 2),
        "already_added": already_added,
        "activated_agent_id": activated_agent_id,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def agent_template_activate(request, template_id):
    """
    POST /api/v1/agents/templates/<id>/activate/
    Creates an agent in the user's workspace from the selected template.

    Template IDs:
      1 — Email Agent
      2 — Research Agent
      3 — Document Agent
      4 — Calendar Agent
      5 — Reporting Agent
    """
    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace found."}, status=status.HTTP_400_BAD_REQUEST)

    template = _TEMPLATE_ID_MAP.get(template_id)
    if not template:
        return Response({"detail": "Template not found."}, status=status.HTTP_404_NOT_FOUND)

    # Allow only one agent per type per workspace
    existing = Agent.objects.filter(
        workspace=workspace, agent_type=template["agent_type"], is_active=True
    ).first()
    if existing:
        return Response(
            {"detail": "You already have a {} in your workspace.".format(template["name"]),
             "agent": AgentSerializer(existing).data},
            status=status.HTTP_200_OK,
        )

    agent = Agent.objects.create(
        workspace=workspace,
        created_by=request.user,
        template_id=template["id"],
        name=template["name"],
        agent_type=template["agent_type"],
        description=template["description"],
        system_prompt=template["system_prompt"],
        tools=template["tools"],
        llm_model=template["llm_model"],
        max_steps=template["max_steps"],
        max_cost_usd=template["max_cost_usd"],
        is_active=True,
    )
    log_event(request, "agent_created", "agent", str(agent.id), workspace,
              {"template_id": template_id, "template": template["slug"]})
    return Response(AgentSerializer(agent).data, status=status.HTTP_201_CREATED)


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
