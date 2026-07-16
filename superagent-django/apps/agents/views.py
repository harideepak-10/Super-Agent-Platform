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
    agents = Agent.objects.filter(
        workspace=workspace,
        is_active=True,
        created_by=request.user,
    ).order_by("created_at")
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
    # ── Email — read ──────────────────────────────────────────────────────────
    "read_email":                    ("Gmail Read",          "mail",         "safe"),
    "summarize_emails":              ("Summarise Emails",    "align-left",   "safe"),
    "read_email_attachment_content": ("Read Attachment",     "file-text",    "safe"),
    "download_attachment":      ("Download File",       "download",          "safe"),
    "read_attachment_content":  ("Read Attachment",    "file-text",         "safe"),
    "extract_data_from_attachment": ("Extract Data",   "layers",            "safe"),
    # ── Email — inbox management ──────────────────────────────────────────────
    "mark_as_read":             ("Mark as Read",       "check-circle",      "safe"),
    "label_email":              ("Label Email",        "tag",               "safe"),
    "move_to_folder":           ("Move to Folder",     "folder",            "safe"),
    "delete_email":             ("Delete Email",       "trash-2",           "high"),
    # ── Email — compose ───────────────────────────────────────────────────────
    "create_draft":             ("Create Draft",       "edit-3",            "safe"),
    "create_gmail_draft":       ("Gmail Draft",        "edit-3",            "safe"),
    "reply_to_email":           ("Reply Email",        "corner-down-left",  "high"),
    "forward_email":            ("Forward Email",      "corner-up-right",   "high"),
    "schedule_email":           ("Schedule Send",      "clock",             "high"),
    "send_email":               ("Gmail Send",         "send",              "high"),
    # ── Email — intelligence ──────────────────────────────────────────────────
    "extract_invoice_data":     ("Extract Invoice",    "file-plus",         "safe"),
    "detect_follow_up_needed":  ("Detect Follow-ups",  "bell",              "safe"),
    # ── Shared — customer memory ──────────────────────────────────────────────
    "list_customer_profiles":   ("Customer Profiles",  "users",             "safe"),
    "search_customer_by_email": ("Find Customer",      "user",              "safe"),
    # ── Calendar — read ───────────────────────────────────────────────────────
    "list_events":              ("List Events",        "calendar",          "safe"),
    "get_event":                ("View Event",         "calendar",          "safe"),
    "find_free_slots":          ("Find Free Slots",    "clock",             "safe"),
    "set_reminder":             ("Set Reminder",       "bell",              "safe"),
    "check_attendee_availability": ("Check Availability", "user-check",     "safe"),
    "detect_conflicts":         ("Detect Conflicts",   "alert-triangle",    "safe"),
    "suggest_meeting_time":     ("Suggest Time",       "clock",             "safe"),
    # ── Calendar — write ──────────────────────────────────────────────────────
    "create_meeting":           ("Create Meeting",     "calendar-plus",     "high"),
    "create_recurring_event":   ("Recurring Event",    "repeat",            "high"),
    "update_event":             ("Update Event",       "edit-3",            "high"),
    "delete_event":             ("Delete Event",       "trash-2",           "high"),
    "respond_to_invite":        ("RSVP",               "check-circle",      "high"),
    "block_focus_time":         ("Block Focus Time",   "shield",            "high"),
    "send_meeting_summary":     ("Meeting Summary",    "send",              "high"),
    # ── Generic ───────────────────────────────────────────────────────────────
    "classify_text":            ("Classify Text",      "tag",               "safe"),
    "web_search":               ("Web Search",         "search",            "safe"),
    "file_read":                ("File Read",          "file",              "safe"),
    "file_write":               ("File Write",         "file-plus",         "medium"),
    "browse_web":               ("Browse Web",         "globe",             "safe"),
    "cal_read":                 ("Calendar Read",      "calendar",          "safe"),
    "cal_write":                ("Calendar Write",     "calendar",          "medium"),
    "delete_file":              ("Delete File",        "trash-2",           "high"),
    "export_csv":               ("Export CSV",         "download",          "safe"),
    "upload_to_drive":          ("Drive Upload",       "upload-cloud",      "safe"),
    "generate_report":          ("Generate Report",    "file-text",         "safe"),
    # ── Document — read ───────────────────────────────────────────────────────
    "read_from_drive":          ("Drive Read",         "folder-open",       "safe"),
    "summarize_document":       ("Summarise Doc",      "file-text",         "safe"),
    "extract_tables":           ("Extract Tables",     "grid",              "safe"),
    "ocr_document":             ("OCR Scan",           "eye",               "safe"),
    # ── Document — create ─────────────────────────────────────────────────────
    "generate_content":         ("Generate Content",   "cpu",               "safe"),
    "create_pdf":               ("Create PDF",         "file",              "safe"),
    "create_docx":              ("Create Word Doc",    "file-text",         "safe"),
    "create_presentation":      ("Create PPTX",        "monitor",           "safe"),
    "fill_template":            ("Fill Template",      "edit-3",            "safe"),
    "merge_pdfs":               ("Merge PDFs",         "layers",            "safe"),
    # ── Document — analyse ────────────────────────────────────────────────────
    "compare_documents":        ("Compare Docs",       "git-diff",          "safe"),
    "translate_document":       ("Translate Doc",      "globe",             "safe"),
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

# ---------------------------------------------------------------------------
# TEMPLATE VERSIONING
# Bump `version` whenever you change tools/system_prompt/llm_model/max_steps.
# The sync mechanism uses this to auto-update existing agent instances.
# ---------------------------------------------------------------------------

# Fields that sync automatically when template version increases:
_SYNC_FIELDS = ["system_prompt", "tools", "llm_model", "max_steps", "max_cost_usd"]

_AGENT_TEMPLATES = [
    {
        "id":          1,
        "version":     26,
        "slug":        "email-agent",
        "name":        "Email Agent",
        "agent_type":  "email",
        "description": "Full email lifecycle — read, summarise, reply, schedule, manage inbox. Requires Gmail connected.",
        "icon":        "mail",
        "icon_bg":     "#B45309",
        "border_color":"#F59E0B",
        "badge":       "Popular",
        "badge_color": "#22C55E",
        "capabilities": [
            "Reads, searches and summarises inbox & spam",
            "Sends replies, forwards, schedules future emails",
            "Downloads and reads email attachments (PDF/DOCX/CSV)",
            "Extracts invoice data, detects follow-ups needed",
            "Creates and saves Gmail drafts for review",
            "Labels, moves and manages inbox organisation",
            "Maintains persistent customer memory & preferences",
        ],
        "tools": [
            # Read
            "read_email", "search_emails", "summarize_emails",
            # Attachments
            "read_email_attachment_content", "download_attachment", "read_attachment_content",
            # Compose
            "send_email", "reply_to_email", "create_draft",
        ],
        "llm_model":    "llama-3.3-70b-versatile",
        "system_prompt": (
            "You are EmailAgent, the KRYPSOS AI assistant for professional email management.\n\n"

            "=== READING & SUMMARISING EMAILS ===\n\n"
            "Trigger for: 'read', 'check', 'summarize', 'show', 'what are my emails', 'any new emails'.\n\n"
            "LIMIT — always 1 unless user gives a number:\n"
            "  'last' or singular 'email' with no number → limit=1\n"
            "  'last N' / 'recent N' / 'N emails' → limit=N\n"
            "  plural 'emails' with no number → limit=5\n\n"
            "  1. Call read_email(limit=<limit>, filter='-in:spam -in:trash')\n"
            "  2. For EACH email (never skip any):\n"
            "     - Summarize body if it has content\n"
            "     - If has_attachments is true → call read_email_attachment_content and summarize\n"
            "     - Do BOTH if body AND attachments\n"
            "  3. Write summary DIRECTLY — do NOT call summarize_emails\n"
            "  STOP. Do NOT call send_email after summarizing.\n\n"

            "ALWAYS format your email summary EXACTLY like this — no deviations:\n\n"
            "Email 1\n"
            "Received from: <sender name and email>\n"
            "Subject: <subject>\n"
            "<2-3 sentence summary of what the email says>\n\n"
            "Email 2\n"
            "Received from: <sender name and email>\n"
            "Subject: <subject>\n"
            "<2-3 sentence summary of what the email says>\n\n"
            "...and so on for every email.\n\n"
            "NEVER use bullet points, icons, or headers. NEVER combine emails. One block per email.\n\n"

            "=== READING ATTACHMENTS ===\n\n"
            "When the user asks about an attachment or wants it summarized:\n"
            "  1. Call read_email_attachment_content with the right filter\n"
            "     e.g. {'filter': 'from:someone@email.com has:attachment', 'limit': 1}\n"
            "  2. The tool returns content for EVERY attachment in the email\n"
            "  3. For EACH attachment, read through ALL pages and write a comprehensive summary\n\n"
            "ALWAYS format your attachment response EXACTLY like this:\n\n"
            "Attachment 1: <filename>\n"
            "<detailed summary — every section, key facts, figures, dates, names, decisions>\n\n"
            "Attachment 2: <filename>\n"
            "<detailed summary>\n\n"
            "IMAGES / SCANNED PAGES:\n"
            "  - If a page says '[This page contains an image or photo]' → write:\n"
            "    'Page N contains an image/photo that cannot be read as text.'\n"
            "  - If the WHOLE file is image-based → write:\n"
            "    'This attachment contains only images or scanned content — text could not be extracted.'\n"
            "  - If SOME pages have text and some are images → summarize the text pages and note the image pages\n"
            "  NEVER skip an attachment entirely. Always report what it is, even if unreadable.\n"
            "  NEVER say 'I cannot read attachments' — you have read_email_attachment_content\n\n"

            "=== READ EMAIL RULES ===\n\n"
            "DEFAULT filter (when user does NOT mention spam or trash): '-in:spam -in:trash'\n"
            "ONLY use 'is:unread' if the user explicitly says 'unread' or 'new emails'.\n\n"
            "  'read my last 5 emails'       → filter: '-in:spam -in:trash', limit: 5\n"
            "  'recent emails'               → filter: '-in:spam -in:trash', limit: 10\n"
            "  'unread emails'               → filter: 'is:unread -in:spam -in:trash', limit: 10\n"
            "  'emails from spam'            → filter: 'in:spam', limit: 10\n"
            "  'unread emails from spam'     → filter: 'in:spam is:unread', limit: 10\n"
            "  'check my spam'               → filter: 'in:spam', limit: 10\n"
            "  NEVER fetch more than 10 unread emails at once — too many tokens.\n"
            "  If user says 'all unread' or implies a large number, still cap at 10 and tell them.\n\n"
            "CRITICAL — If read_email returns 0 emails or an empty list:\n"
            "  → Immediately call search_emails(query='in:inbox', max_results=10)\n"
            "  → Only say 'no emails found' if search_emails ALSO returns 0 results\n\n"

            "=== SEND EMAIL RULE ===\n"
            "NEVER call send_email unless the user explicitly says to send to someone.\n"
            "  'read my emails'      → NO send_email\n"
            "  'summarize my emails' → NO send_email\n"
            "  'send the summary to john@example.com' → YES send_email(to='john@example.com', ...)\n\n"

            "=== DRAFTING A REPLY ===\n"
            "  1. read_email or search_emails\n"
            "  2. [approval] → send_email / reply_to_email\n\n"

            "=== YELLOW zone (require human approval) ===\n"
            "send_email, reply_to_email\n\n"

            "=== GREEN zone (run automatically) ===\n"
            "read_email, search_emails, summarize_emails,\n"
            "read_email_attachment_content, download_attachment, read_attachment_content,\n"
            "create_draft\n\n"

            "=== HARD RULES ===\n"
            "- NEVER invent email content — only use what tools return\n"
            "- NEVER use placeholder names like 'Subject 1', 'Sender 1'\n"
            "- NEVER call send_email just because you summarized emails\n"
            "- NEVER put a placeholder as send_email body — always use real content\n"
            "- For meeting scheduling, direct user to Calendar Agent"
        ),
        "max_steps":    8,
        "max_cost_usd": 1.0,
    },
    {
        "id":          2,
        "version":     4,
        "slug":        "document-agent",
        "name":        "Document Agent",
        "agent_type":  "document",
        "description": "Full document lifecycle — read from Drive, summarise, create PDFs/DOCX/PPTX, OCR, compare versions, translate, and upload back to Drive.",
        "icon":        "file-text",
        "icon_bg":     "#0F766E",
        "border_color":"#14B8A6",
        "badge":       None,
        "badge_color": None,
        "capabilities": [
            "Reads and lists files from Google Drive",
            "Summarises documents and extracts key points & action items",
            "Extracts tables from PDFs and Word documents",
            "Runs OCR on scanned PDFs to extract text",
            "Generates structured PDFs, Word docs, and PowerPoint presentations",
            "Fills Word templates with dynamic data (invoices, letters, reports)",
            "Merges multiple PDF files into one",
            "Compares two document versions and highlights differences",
            "Translates documents to Tamil, Hindi, French, Spanish, and 10+ languages",
            "Exports data as CSV spreadsheets",
            "Uploads completed documents to Google Drive",
        ],
        "tools": [
            # Read
            "read_from_drive",
            "summarize_document",
            "extract_tables",
            "ocr_document",
            # Create
            "generate_content",
            "create_pdf",
            "create_docx",
            "create_presentation",
            "fill_template",
            "merge_pdfs",
            "export_csv",
            # Analyse
            "compare_documents",
            "translate_document",
            # Save
            "upload_to_drive",
        ],
        "llm_model":   "llama-3.3-70b-versatile",
        "system_prompt": (
            "You are DocumentAgent, the KRYPSOS AI assistant for the full document lifecycle.\n\n"
            "## READ tools (GREEN — run automatically):\n"
            "- read_from_drive: list or download files from Google Drive\n"
            "- summarize_document: extract key points and action items from any file\n"
            "- extract_tables: pull tables from PDF or Word documents\n"
            "- ocr_document: extract text from scanned PDFs using OCR\n\n"
            "## CREATE tools (GREEN — run automatically):\n"
            "- generate_content: LLM generates structured document sections — call FIRST\n"
            "- create_pdf: build a PDF from sections\n"
            "- create_docx: build a Word .docx from sections\n"
            "- create_presentation: build a PowerPoint .pptx slide deck\n"
            "- fill_template: populate a Word template with {{FIELD}} placeholders\n"
            "- merge_pdfs: combine multiple PDFs into one\n"
            "- export_csv: create a CSV from tabular data\n\n"
            "## ANALYSE tools (GREEN — run automatically):\n"
            "- compare_documents: diff two file versions and show what changed\n"
            "- translate_document: translate file to Tamil, Hindi, French, Spanish, etc.\n\n"
            "## SAVE tool (YELLOW — requires human approval):\n"
            "- upload_to_drive: save completed file to Google Drive\n\n"
            "## Rules:\n"
            "1. For CREATE tasks: call generate_content first, then the format tool.\n"
            "2. For READ tasks: call read_from_drive or summarize_document directly.\n"
            "3. upload_to_drive is YELLOW — always explain and wait for approval.\n"
            "4. After Drive upload, include drive_url in your final answer.\n"
            "5. If Drive is not connected, still create the local file and share the path."
        ),
        "max_steps":   20,
        "max_cost_usd": 1.0,
    },
    {
        "id":          3,
        "version":     5,
        "slug":        "calendar-agent",
        "name":        "Calendar Agent",
        "agent_type":  "calendar",
        "description": "Full Google Calendar management — view, schedule, reschedule, cancel, RSVP, reminders, recurring events, conflict detection, and smart scheduling.",
        "icon":        "calendar",
        "icon_bg":     "#065F46",
        "border_color":"#10B981",
        "badge":       None,
        "badge_color": None,
        "capabilities": [
            "Lists and views upcoming events",
            "Finds free time slots and suggests best meeting times for all attendees",
            "Checks attendee availability before booking",
            "Detects scheduling conflicts in your calendar",
            "Creates meetings with Google Meet links and attendee invites",
            "Creates recurring (daily/weekly/monthly) events",
            "Reschedules and updates existing events",
            "Cancels events and notifies attendees",
            "Accepts/declines meeting invitations",
            "Sets popup and email reminders on any event",
            "Blocks focus time / Do Not Disturb periods",
            "Sends meeting summaries and agendas to attendees",
        ],
        "tools": [
            "list_events",
            "get_event",
            "find_free_slots",
            "set_reminder",
            "check_attendee_availability",
            "detect_conflicts",
            "suggest_meeting_time",
            "create_meeting",
            "create_recurring_event",
            "update_event",
            "delete_event",
            "respond_to_invite",
            "block_focus_time",
            "send_meeting_summary",
            "search_customer_by_email",
            "web_search",
        ],
        "llm_model":   "llama-3.3-70b-versatile",
        "system_prompt": (
            "You are CalendarAgent, the KRYPSOS AI assistant for Google Calendar management.\n\n"
            "## READ tools (GREEN — run automatically):\n"
            "- list_events: list upcoming events\n"
            "- get_event: full event details by ID or title\n"
            "- find_free_slots: check your own availability\n"
            "- check_attendee_availability: check if ALL attendees are free for a proposed time\n"
            "- detect_conflicts: find overlapping events in your calendar\n"
            "- suggest_meeting_time: find best slot for all attendees automatically\n"
            "- set_reminder: add reminder to event or create standalone reminder\n\n"
            "## WRITE tools (YELLOW — require human approval):\n"
            "- create_meeting: create event with Meet link + invite attendees\n"
            "- create_recurring_event: create daily/weekly/monthly repeating event\n"
            "- update_event: reschedule or modify event\n"
            "- delete_event: cancel event, notify attendees\n"
            "- respond_to_invite: accept/decline/tentative RSVP\n"
            "- block_focus_time: create focus/DND block\n"
            "- send_meeting_summary: email summary/agenda to all attendees\n\n"
            "## Lookup:\n"
            "- search_customer_by_email: find attendee email by name\n"
            "- web_search: timezone or location lookup\n\n"
            "## Rules:\n"
            "1. Always call current_time first when user mentions relative times.\n"
            "2. Use search_customer_by_email if you only have a name, not email.\n"
            "3. Use suggest_meeting_time before create_meeting when scheduling with others.\n"
            "4. Use check_attendee_availability to verify a specific proposed time.\n"
            "5. For YELLOW tools, explain what you will do and wait for approval.\n"
            "6. Default timezone: Asia/Kolkata (IST)."
        ),
        "max_steps":   20,
        "max_cost_usd": 1.0,
    },
]

_TEMPLATE_MAP           = {t["slug"]:       t for t in _AGENT_TEMPLATES}
_TEMPLATE_ID_MAP        = {t["id"]:         t for t in _AGENT_TEMPLATES}
_TEMPLATE_AGENT_TYPE_MAP = {t["agent_type"]: t for t in _AGENT_TEMPLATES}


def sync_agent_from_template(agent, template: dict) -> bool:
    """Sync an Agent instance from its template dict.

    Updates system_prompt, tools, llm_model, max_steps, max_cost_usd,
    and template_version. Returns True if any field was changed.
    """
    changed = False
    for field in _SYNC_FIELDS:
        new_val = template.get(field)
        if new_val is not None and getattr(agent, field) != new_val:
            setattr(agent, field, new_val)
            changed = True

    new_version = template.get("version", 0)
    if agent.template_version != new_version:
        agent.template_version = new_version
        changed = True

    if changed:
        agent.save(update_fields=_SYNC_FIELDS + ["template_version"])
    return changed


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_templates(request):
    """
    GET /api/v1/agents/templates/
    Returns the available agent templates (Email, Calendar, Document).
    already_added=true means the user has already activated that agent.
    agent_id is set when activated — use it to navigate to that agent.
    """
    workspace = _get_workspace(request)

    activated_map = {}
    if workspace:
        for agent in Agent.objects.filter(
            workspace=workspace,
            is_active=True,
            created_by=request.user,
        ).exclude(template_id=None):
            activated_map[agent.agent_type] = agent

    result = []
    for t in _AGENT_TEMPLATES:
        activated_agent = activated_map.get(t["agent_type"])
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
            "already_added": activated_agent is not None,
            "agent_id":      str(activated_agent.id) if activated_agent else None,
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
        template_version=template.get("version", 0),   # track version at activation
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
# Template Sync
# ---------------------------------------------------------------------------

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def agent_template_sync(request, template_id):
    """
    POST /api/v1/agents/templates/<id>/sync/

    Force-sync all Agent instances (in the current workspace) that were
    created from this template to the latest template version.

    Synced fields: system_prompt, tools, llm_model, max_steps, max_cost_usd.
    NOT synced: name, description (user may have customised these).

    Returns:
        {
            "synced":    2,       # number of agents updated
            "skipped":   0,       # already on latest version
            "template":  "email-agent",
            "version":   3
        }
    """
    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace found."}, status=status.HTTP_400_BAD_REQUEST)

    template = _TEMPLATE_ID_MAP.get(template_id)
    if not template:
        return Response({"detail": "Template not found."}, status=status.HTTP_404_NOT_FOUND)

    agents = Agent.objects.filter(workspace=workspace, template_id=template_id, is_active=True)
    synced  = 0
    skipped = 0
    for agent in agents:
        if sync_agent_from_template(agent, template):
            synced += 1
        else:
            skipped += 1

    return Response({
        "synced":   synced,
        "skipped":  skipped,
        "template": template["slug"],
        "version":  template["version"],
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def agent_sync_all_templates(request):
    """
    POST /api/v1/agents/sync-all-templates/

    Sync ALL template-based agents in the workspace to their latest versions.
    Call this after deploying code changes that update any template.

    Returns:
        {
            "results": [
                {"template": "email-agent", "version": 3, "synced": 1, "skipped": 0},
                ...
            ],
            "total_synced": 2
        }
    """
    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace found."}, status=status.HTTP_400_BAD_REQUEST)

    results = []
    total_synced = 0
    for template in _AGENT_TEMPLATES:
        agents = Agent.objects.filter(workspace=workspace, template_id=template["id"], is_active=True)
        synced  = 0
        skipped = 0
        for agent in agents:
            if sync_agent_from_template(agent, template):
                synced += 1
            else:
                skipped += 1
        results.append({
            "template": template["slug"],
            "version":  template["version"],
            "synced":   synced,
            "skipped":  skipped,
        })
        total_synced += synced

    return Response({"results": results, "total_synced": total_synced})


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
                    "value":   "llama-3.3-70b-versatile",
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
            "default": "llama-3.3-70b-versatile",
        },
        "tools": {
            "available": [
                {"name": "web_search",     "label": "Web Search",    "icon": "search",       "risk": "safe"},
                {"name": "file_read",      "label": "File Read",     "icon": "file",         "risk": "safe"},
                {"name": "file_write",     "label": "File Write",    "icon": "file-plus",    "risk": "medium"},
                {"name": "read_email",     "label": "Gmail Read",    "icon": "mail",         "risk": "safe"},
                {"name": "send_email",     "label": "Gmail Send",    "icon": "send",         "risk": "high"},
                {"name": "browse_web",     "label": "Browse Web",    "icon": "globe",        "risk": "safe"},
                {"name": "cal_read",          "label": "Cal Read",         "icon": "calendar",   "risk": "safe"},
                {"name": "cal_write",         "label": "Cal Write",        "icon": "calendar",   "risk": "medium"},
                {"name": "list_events",       "label": "List Events",      "icon": "calendar",   "risk": "safe"},
                {"name": "get_event",         "label": "Get Event",        "icon": "calendar",   "risk": "safe"},
                {"name": "find_free_slots",   "label": "Free Slots",       "icon": "clock",      "risk": "safe"},
                {"name": "set_reminder",      "label": "Set Reminder",     "icon": "bell",       "risk": "safe"},
                {"name": "create_meeting",    "label": "Create Meeting",   "icon": "calendar",   "risk": "medium"},
                {"name": "update_event",      "label": "Update Event",     "icon": "edit-2",     "risk": "medium"},
                {"name": "delete_event",      "label": "Delete Event",     "icon": "trash-2",    "risk": "medium"},
                {"name": "respond_to_invite", "label": "RSVP",             "icon": "check",      "risk": "medium"},
                {"name": "classify_text",     "label": "Classify Text",    "icon": "tag",        "risk": "safe"},
                {"name": "create_draft",   "label": "Create Draft",  "icon": "edit-3",       "risk": "safe"},
                {"name": "export_csv",     "label": "Export CSV",    "icon": "download",     "risk": "safe"},
                {"name": "generate_report","label": "Gen Report",    "icon": "file-text",    "risk": "safe"},
            ],
            "risk_note": "Tools marked high will always require your approval before running.",
        },
        "guardrails": {
            "max_steps": {"label": "Max Steps", "subtitle": "Iteration limit per request",
                          "icon": "repeat", "default": 19, "min": 1, "max": 50},
            "max_cost_usd": {"label": "Max Cost (USD)", "subtitle": "Maximum budget per run",
                             "icon": "dollar-sign", "default": 1.00, "min": 0.10,
                             "max": 50.00, "step": 0.10},
        },
        "submit_label": "Create Agent",
        "submit_url":   "/api/v1/agents/create/",
        "submit_method": "POST",
    })
