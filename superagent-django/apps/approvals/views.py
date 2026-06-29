from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Approval, ApprovalRule
from .serializers import ApprovalSerializer, ApprovalDecisionSerializer, ApprovalRuleSerializer
from apps.audit.utils import log_event


# ---------------------------------------------------------------------------
# Tool metadata registry
# ---------------------------------------------------------------------------

_TOOL_META = {
    "publish_post":    ("Publish Blog Post",    "This will publish content publicly.",          "globe",        "high"),
    "publish_page":    ("Publish Page",         "This will make a page publicly visible.",      "globe",        "high"),
    "delete_file":     ("Delete File",          "Permanently deletes a file from Drive.",       "trash",        "high"),
    "send_email":      ("Send Email",           "Sends an email on your behalf.",               "mail",         "high"),
    "send_alert":      ("Send Telegram Alert",  "Sends a message to your Telegram channel.",    "bell",         "medium"),
    "flag_invoice":    ("Flag Invoice",         "Marks an invoice for manual review.",          "flag",         "medium"),
    "export_csv":      ("Export CSV",           "Exports data to a downloadable CSV file.",     "download",     "low"),
    "generate_report": ("Generate Report",      "Creates and saves a new report document.",     "file-text",    "low"),
    "upload_to_drive": ("Upload to Drive",      "Uploads a file to Google Drive.",              "upload-cloud", "low"),
}

_DEFAULT_META = ("Perform Action", "This action requires your approval before it runs.", "zap", "medium")

_RISK_LABELS = {
    "high":   ("HIGH RISK ACTION",  "This action is irreversible or publicly visible."),
    "medium": ("APPROVAL REQUIRED", "This action requires your review before it runs."),
    "low":    ("REVIEW ACTION",     "Please confirm this action before it continues."),
}

_AGENT_ICON = {
    "email":      ("mail",         "#EF4444"),
    "finance":    ("bar-chart",    "#F59E0B"),
    "document":   ("folder",       "#F59E0B"),
    "reporting":  ("file-text",    "#8B5CF6"),
    "compliance": ("shield",       "#F59E0B"),
    "qa":         ("check-circle", "#10B981"),
    "custom":     ("cpu",          "#6B7280"),
}
_DEFAULT_ICON = ("zap", "#6B7280")


def _tool_meta(tool_name):
    display, description, icon, risk = _TOOL_META.get(tool_name, _DEFAULT_META)
    risk_label, risk_desc = _RISK_LABELS[risk]
    return {
        "display_name":     display,
        "description":      description,
        "icon":             icon,
        "risk_level":       risk,
        "risk_label":       risk_label,
        "risk_description": risk_desc,
    }


def _human_ago(dt):
    seconds = int((timezone.now() - dt).total_seconds())
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        m = seconds // 60
        return "%d min ago" % m
    elif seconds < 86400:
        h = seconds // 3600
        return "%d hour%s ago" % (h, "s" if h > 1 else "")
    d = seconds // 86400
    return "%d day%s ago" % (d, "s" if d > 1 else "")


def _expires_label(expires_at):
    """Returns '23m', '4h 12m', or 'Expired'."""
    if not expires_at:
        return None
    secs = int((expires_at - timezone.now()).total_seconds())
    if secs <= 0:
        return "Expired"
    if secs < 3600:
        return "%dm" % (secs // 60)
    h = secs // 3600
    m = (secs % 3600) // 60
    if m:
        return "%dh %dm" % (h, m)
    return "%dh" % h


def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def approval_list(request):
    workspace = _get_workspace(request)
    approvals = Approval.objects.filter(task__workspace=workspace).order_by("-created_at")
    status_filter = request.query_params.get("status")
    if status_filter:
        approvals = approvals.filter(status=status_filter)
    return Response(ApprovalSerializer(approvals, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def approval_detail(request, pk):
    workspace = _get_workspace(request)
    approval = get_object_or_404(Approval, id=pk, task__workspace=workspace)
    return Response(ApprovalSerializer(approval).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def approval_decide(request, pk):
    from apps.tasks.tasks import resume_agent_task

    workspace = _get_workspace(request)
    approval = get_object_or_404(
        Approval, id=pk, task__workspace=workspace, status=Approval.Status.PENDING
    )

    serializer = ApprovalDecisionSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    approved = serializer.validated_data["approved"]
    note = serializer.validated_data["note"]

    approval.status = Approval.Status.APPROVED if approved else Approval.Status.REJECTED
    approval.reviewer = request.user
    approval.reviewer_note = note
    approval.reviewed_at = timezone.now()
    approval.save(update_fields=["status", "reviewer", "reviewer_note", "reviewed_at"])

    event = "approval_granted" if approved else "approval_rejected"
    log_event(request, event, "approval", str(approval.id), workspace)

    resume_agent_task.delay(str(approval.task_id), str(approval.id), approved, note)

    return Response(ApprovalSerializer(approval).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def approval_review_detail(request, pk):
    """
    GET /api/v1/approvals/{id}/review/
    "Review Action" screen — agent card, risk banner, action card, timing.
    """
    workspace = _get_workspace(request)
    approval = get_object_or_404(
        Approval.objects.select_related("task", "task__agent"),
        id=pk,
        task__workspace=workspace,
    )

    task = approval.task
    agent = task.agent
    meta = _tool_meta(approval.tool_name)

    expires_in = None
    if approval.expires_at:
        delta = (approval.expires_at - timezone.now()).total_seconds()
        expires_in = max(0, int(delta))

    return Response({
        "id":     str(approval.id),
        "status": approval.status,
        "agent": {
            "id":         str(agent.id) if agent else None,
            "name":       agent.name if agent else "Agent",
            "agent_type": agent.agent_type if agent else "custom",
            "is_active":  agent.is_active if agent else False,
        },
        "risk": {
            "level":       meta["risk_level"],
            "label":       meta["risk_label"],
            "description": meta["risk_description"],
        },
        "action": {
            "tool_name":    approval.tool_name,
            "display_name": meta["display_name"],
            "description":  meta["description"],
            "icon":         meta["icon"],
            "tool_input":   approval.tool_input,
            "tool_zone":    approval.tool_zone,
        },
        "task": {
            "id":     str(task.id),
            "prompt": task.prompt[:200],
        },
        "timing": {
            "requested_ago":      _human_ago(approval.created_at),
            "requested_at":       approval.created_at.isoformat(),
            "expires_in_seconds": expires_in,
            "expires_at":         approval.expires_at.isoformat() if approval.expires_at else None,
        },
        "can_decide": approval.status == Approval.Status.PENDING,
        "is_expired": expires_in == 0 if expires_in is not None else False,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def approval_confirm(request, pk):
    """
    GET /api/v1/approvals/{id}/confirm/
    "Approve this action?" confirmation modal.
    """
    workspace = _get_workspace(request)
    approval = get_object_or_404(
        Approval.objects.select_related("task", "task__agent"),
        id=pk,
        task__workspace=workspace,
        status=Approval.Status.PENDING,
    )

    task  = approval.task
    agent = task.agent
    meta  = _tool_meta(approval.tool_name)
    user  = request.user

    membership = user.memberships.filter(workspace=workspace).first()
    role_map = {
        "owner":  "Workspace Owner",
        "admin":  "Admin",
        "member": "Member",
        "viewer": "Viewer",
    }
    role_display = role_map.get(membership.role if membership else "", "Team Member")

    name = user.name or user.email.split("@")[0]
    parts = name.split()
    if len(parts) >= 2:
        initials = (parts[0][0] + parts[-1][0]).upper()
        display_name = parts[0] + " " + parts[-1][0] + "."
    else:
        initials = name[:2].upper()
        display_name = name

    cost_so_far = float(task.cost_usd)
    irrevocable = meta["risk_level"] in ("high", "medium")
    agent_name = agent.name if agent else "Agent"
    prompt_short = task.prompt[:80].rstrip(".")

    return Response({
        "id":     str(approval.id),
        "status": approval.status,
        "summary": agent_name + " will execute: " + prompt_short + ".",
        "details": {
            "action":     meta["display_name"],
            "agent_name": agent_name,
            "risk_level": meta["risk_level"].upper(),
            "cost_eur":   round(cost_so_far, 2),
            "tool_zone":  approval.tool_zone,
        },
        "reviewer": {
            "name":       display_name,
            "full_name":  name,
            "initials":   initials,
            "role":       role_display,
            "avatar_url": user.avatar_url or None,
            "is_owner":   (membership.role == "owner") if membership else False,
        },
        "warning": (
            "This action cannot be undone once the agent begins the operation."
            if irrevocable else
            "The agent will execute this action immediately after approval."
        ),
        "irrevocable":   irrevocable,
        "confirm_label": "Yes, Approve",
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def approval_inbox(request):
    """
    GET /api/v1/approvals/inbox/
    Approvals Inbox screen — pending list with urgency indicators, expiry
    countdowns, colored borders and dot accents.

    Query params:
      ?urgent_only=true   — only items expiring in <30 min or high-risk
    """
    import datetime as dt

    workspace = _get_workspace(request)

    qs = (
        Approval.objects
        .filter(task__workspace=workspace, status=Approval.Status.PENDING)
        .select_related("task", "task__agent")
        .order_by("expires_at", "created_at")
    )

    if request.query_params.get("urgent_only") == "true":
        cutoff = timezone.now() + dt.timedelta(minutes=30)
        qs = qs.filter(expires_at__lte=cutoff)

    items = []
    urgent_count = 0

    for ap in qs:
        task  = ap.task
        agent = task.agent
        meta  = _tool_meta(ap.tool_name)

        agent_type = agent.agent_type if agent else "custom"
        icon_name, icon_color = _AGENT_ICON.get(agent_type, _DEFAULT_ICON)

        expires_secs = None
        if ap.expires_at:
            expires_secs = int((ap.expires_at - timezone.now()).total_seconds())

        is_urgent = (
            meta["risk_level"] == "high"
            or (expires_secs is not None and 0 < expires_secs < 1800)
        )
        is_expired = expires_secs is not None and expires_secs <= 0

        if is_urgent:
            urgent_count += 1

        if is_expired:
            accent = "#9CA3AF"
        elif is_urgent:
            accent = "#EF4444"
        elif meta["risk_level"] == "medium":
            accent = "#F59E0B"
        else:
            accent = "#10B981"

        items.append({
            "id":      str(ap.id),
            "task_id": str(task.id),
            "agent": {
                "name":       agent.name if agent else "Agent",
                "agent_type": agent_type,
                "icon":       icon_name,
                "icon_color": icon_color,
            },
            "action_summary": task.prompt[:80],
            "tool_display":   meta["display_name"],
            "risk_level":     meta["risk_level"],
            "is_urgent":      is_urgent,
            "is_expired":     is_expired,
            "accent_color":   accent,
            "expires_label":      _expires_label(ap.expires_at),
            "expires_in_seconds": max(0, expires_secs) if expires_secs is not None else None,
            "expires_at":         ap.expires_at.isoformat() if ap.expires_at else None,
            "requested_ago": _human_ago(ap.created_at),
            "created_at":    ap.created_at.isoformat(),
        })

    total = len(items)
    return Response({
        "total_pending": total,
        "urgent_count":  urgent_count,
        "subtitle": "%d action%s awaiting approval" % (total, "s" if total != 1 else ""),
        "approvals": items,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def pending_approvals(request):
    workspace = _get_workspace(request)
    approvals = Approval.objects.filter(
        task__workspace=workspace, status=Approval.Status.PENDING
    ).order_by("-created_at")
    return Response(ApprovalSerializer(approvals, many=True).data)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def approval_rules(request):
    workspace = _get_workspace(request)
    if request.method == "GET":
        rules = ApprovalRule.objects.filter(workspace=workspace)
        return Response(ApprovalRuleSerializer(rules, many=True).data)

    serializer = ApprovalRuleSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    rule = serializer.save(workspace=workspace, created_by=request.user)
    return Response(ApprovalRuleSerializer(rule).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def approval_rule_detail(request, pk):
    workspace = _get_workspace(request)
    rule = get_object_or_404(ApprovalRule, id=pk, workspace=workspace)

    if request.method == "DELETE":
        rule.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = ApprovalRuleSerializer(rule, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(ApprovalRuleSerializer(rule).data)
