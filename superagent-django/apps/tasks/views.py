import json
import re
import threading

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Task, TaskStep
from .serializers import TaskSerializer, TaskListSerializer, CreateTaskSerializer, TaskStepSerializer
from apps.audit.utils import log_event


# ── Drive file-selection helpers ───────────────────────────────────────────────

_VAGUE_READ_WORDS = {"summarize", "summarise", "read", "extract", "review",
                     "analyse", "analyze", "open", "show", "get", "what"}
_VAGUE_FILE_WORDS = {"document", "file", "pdf", "doc", "drive"}
# A specific filename has an extension or is quoted
_HAS_FILENAME_RE  = re.compile(r'\.[a-zA-Z]{2,5}\b|"[^"]+"', re.IGNORECASE)


def _is_vague_drive_request(prompt: str) -> bool:
    """Return True when the user wants to read/summarize a Drive file but hasn't named it."""
    lower = prompt.lower()
    if not any(w in lower for w in _VAGUE_READ_WORDS):
        return False
    if not any(w in lower for w in _VAGUE_FILE_WORDS):
        return False
    if _HAS_FILENAME_RE.search(prompt):
        return False  # already has a specific filename
    return True


def _list_drive_files_for_workspace(workspace) -> list | None:
    """Fetch up to 20 recent Drive files. Returns None when Drive is not connected."""
    try:
        import os
        from apps.integrations.models import Integration
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        integration = Integration.objects.filter(
            workspace=workspace,
            provider=Integration.Provider.GOOGLE_DRIVE,
            status=Integration.Status.ACTIVE,
        ).first()
        if not integration or not integration.access_token:
            return None

        creds = Credentials(
            token=integration.access_token,
            refresh_token=integration.refresh_token,
            client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            token_uri="https://oauth2.googleapis.com/token",
        )
        service = build("drive", "v3", credentials=creds)
        result = service.files().list(
            q="trashed = false",
            pageSize=20,
            fields="files(id, name, mimeType, size, modifiedTime)",
            orderBy="modifiedTime desc",
        ).execute()

        _MIME_LABELS = {
            "application/vnd.google-apps.document":     "Google Doc",
            "application/vnd.google-apps.spreadsheet":  "Google Sheet",
            "application/vnd.google-apps.presentation": "Google Slides",
            "application/pdf":                          "PDF",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word (.docx)",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":       "Excel (.xlsx)",
            "text/plain":                               "Text",
        }

        files = []
        for f in result.get("files", []):
            size_bytes = int(f.get("size", 0) or 0)
            files.append({
                "file_id":  f.get("id", ""),
                "name":     f.get("name", ""),
                "type":     _MIME_LABELS.get(f.get("mimeType", ""), "File"),
                "size_kb":  round(size_bytes / 1024, 1),
                "modified": f.get("modifiedTime", ""),
            })
        return files
    except Exception:
        return None


def _ask_clarification(prompt: str) -> str:
    """Call Groq to generate a relevant clarifying question for a vague prompt."""
    try:
        from groq import Groq
        import os
        client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant. The user gave a very short or vague task. "
                        "Ask ONE short, friendly clarifying question to understand what they need. "
                        "Do not explain yourself. Just ask the question. Max 2 sentences."
                    ),
                },
                {
                    "role": "user",
                    "content": f'The user said: "{prompt}"',
                },
            ],
            max_tokens=80,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "Could you give me a bit more detail about what you'd like me to do?"


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
    prompt = serializer.validated_data["prompt"].strip()

    # Reject vague prompts and ask for clarification
    if len(prompt.split()) < 4:
        clarification = _ask_clarification(prompt)
        return Response(
            {"detail": "needs_clarification", "message": clarification},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # If user wants to read/summarize a Drive file but hasn't named one,
    # list their Drive files so they can pick the right one.
    if _is_vague_drive_request(prompt):
        drive_files = _list_drive_files_for_workspace(workspace)
        if drive_files is not None:
            return Response(
                {
                    "detail": "needs_file_selection",
                    "message": (
                        "Here are the files in your Google Drive. "
                        "Which one would you like me to work with?"
                    ),
                    "files": drive_files,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    task = Task.objects.create(
        workspace=workspace,
        agent=agent,
        created_by=request.user,
        prompt=prompt,
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


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def task_draft_update(request, pk):
    """
    PATCH /api/v1/tasks/<task_id>/draft/

    Edit the email content and/or add attachments BEFORE the user approves
    a YELLOW (send_email) action.  Works in two modes:

    ─────────────────────────────────────────────────────────────────────────
    MODE A — Pending approval (the main use-case)
    ─────────────────────────────────────────────────────────────────────────
    When the task is sitting at WAITING_APPROVAL because the agent wants to
    send an email, this endpoint updates the Approval record's tool_input
    so that when the user hits APPROVE the email goes out with the edited
    content + uploaded attachments.

    Flow:
        1. User creates task: "send email to hari@gmail.com saying this is testing"
        2. Agent prepares the email → YELLOW → task pauses (waiting_approval)
        3. User calls PATCH /tasks/<task_id>/draft/ — edits body/subject/to, adds files
        4. User approves via POST /approvals/<approval_id>/approve/
        5. Email sends with the edited content + attachments

    ─────────────────────────────────────────────────────────────────────────
    MODE B — Gmail draft fallback
    ─────────────────────────────────────────────────────────────────────────
    If the task used create_gmail_draft to build a draft first, the endpoint
    updates that Gmail draft in-place (original behaviour).

    ─────────────────────────────────────────────────────────────────────────
    Request (multipart/form-data or JSON):
        to         — recipient email  (optional)
        subject    — email subject    (optional)
        body       — email body text  (optional)
        files      — one or more file attachments (PDF, JPG, PNG, DOCX, …)

    Response:
        {
            "mode":          "approval" | "gmail_draft",
            "approval_id":   "<uuid>",           // only in approval mode
            "draft_id":      "r123…",            // only in gmail_draft mode
            "to":            "hari@gmail.com",
            "subject":       "Test",
            "body_preview":  "This is testing…",
            "attachments":   ["invoice.pdf"],
            "updated":       true,
            "next_step":     "Approve the task to send the email."
        }
    """
    import base64
    import os
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders
    from django.conf import settings as django_settings

    workspace = _get_workspace(request)
    task = get_object_or_404(Task, id=pk, workspace=workspace)

    # ── incoming fields ───────────────────────────────────────────────────
    new_to      = (request.data.get("to") or "").strip()
    new_subject = (request.data.get("subject") or "").strip()
    new_body    = (request.data.get("body") or "").strip()
    uploaded    = request.FILES.getlist("files")

    # ══════════════════════════════════════════════════════════════════════
    # MODE A — pending send_email approval
    # ══════════════════════════════════════════════════════════════════════
    try:
        from apps.approvals.models import Approval

        # Email tools that go through YELLOW approval
        EMAIL_SEND_TOOLS = [
            "send_email",
            "reply_email",
            "reply_to_email",
            "forward_email",
        ]

        pending_approval = (
            Approval.objects
            .filter(
                task=task,
                tool_name__in=EMAIL_SEND_TOOLS,
                status=Approval.Status.PENDING,
            )
            .order_by("-created_at")
            .first()
        )
    except Exception:
        pending_approval = None

    if pending_approval:
        tool_input = dict(pending_approval.tool_input or {})

        # Update text fields — keep originals when caller doesn't supply them
        if new_to:
            tool_input["to"] = new_to
        if new_subject:
            tool_input["subject"] = new_subject
        if new_body:
            tool_input["body"] = new_body

        # ── Save uploaded files to a stable per-task directory ────────────
        attachment_names = [a.get("filename", "") for a in tool_input.get("attachment_paths", [])]

        if uploaded:
            # Store under MEDIA_ROOT/task_attachments/<task_id>/
            base_dir = os.path.join(
                getattr(django_settings, "MEDIA_ROOT", "/tmp"),
                "task_attachments",
                str(task.id),
            )
            os.makedirs(base_dir, exist_ok=True)

            existing_paths = tool_input.get("attachment_paths", [])
            for f in uploaded:
                safe_name = os.path.basename(f.name)
                dest_path = os.path.join(base_dir, safe_name)
                with open(dest_path, "wb") as fh:
                    for chunk in f.chunks():
                        fh.write(chunk)
                existing_paths.append({"path": dest_path, "filename": safe_name})
                attachment_names.append(safe_name)

            tool_input["attachment_paths"] = existing_paths

        pending_approval.tool_input = tool_input
        pending_approval.save(update_fields=["tool_input"])

        return Response({
            "mode":         "approval",
            "approval_id":  str(pending_approval.id),
            "to":           tool_input.get("to", ""),
            "subject":      tool_input.get("subject", ""),
            "body_preview": (tool_input.get("body") or "")[:200],
            "attachments":  attachment_names,
            "updated":      True,
            "next_step":    "Approve the task to send the email with your changes.",
        })

    # ══════════════════════════════════════════════════════════════════════
    # MODE B — Gmail draft update (task used create_gmail_draft earlier)
    # ══════════════════════════════════════════════════════════════════════
    draft_id         = None
    original_to      = ""
    original_subject = ""

    # Search TaskSteps for a create_gmail_draft result
    for tool_name_filter in (
        ["create_gmail_draft"],
        ["create_draft", "schedule_email"],
    ):
        step = (
            task.steps
            .filter(tool_name__in=tool_name_filter)
            .order_by("-created_at")
            .first()
        )
        if step and step.tool_output:
            try:
                out = step.tool_output
                if isinstance(out, str):
                    out = json.loads(out)
                draft_id         = out.get("draft_id")
                original_to      = out.get("to", "")
                original_subject = out.get("subject", "")
                if draft_id:
                    break
            except Exception:
                pass

    # Fall back to task.result
    if not draft_id and task.result:
        try:
            result_data = json.loads(task.result)
            if isinstance(result_data, dict):
                draft_id         = result_data.get("draft_id")
                original_to      = result_data.get("to", "")
                original_subject = result_data.get("subject", "")
        except Exception:
            pass

    if not draft_id:
        return Response(
            {
                "detail": (
                    "No pending email approval or Gmail draft found for this task. "
                    "The task must be in WAITING_APPROVAL state (agent preparing to send email) "
                    "or have used create_gmail_draft tool."
                ),
                "hint": (
                    "Create a task like 'send email to X saying Y' — "
                    "once the agent pauses for approval, call this endpoint to edit before approving."
                ),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # ── Build updated Gmail draft ─────────────────────────────────────────
    to      = new_to      or original_to
    subject = new_subject or original_subject

    try:
        from core.tools.gmail.auth import GmailAuth
        service = GmailAuth().build_service("default")

        # Fetch existing draft body if caller didn't provide new body
        body = new_body
        if not body:
            existing = service.users().drafts().get(
                userId="me", id=draft_id, format="full"
            ).execute()
            msg     = existing.get("message", {})
            payload = msg.get("payload", {})
            for part in payload.get("parts", [payload]):
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
                    break

        # Build MIME message
        attachment_names = []
        if uploaded:
            mime_msg = MIMEMultipart()
            mime_msg["to"]      = to
            mime_msg["subject"] = subject
            mime_msg.attach(MIMEText(body, "plain"))
            for f in uploaded:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f'attachment; filename="{f.name}"')
                mime_msg.attach(part)
                attachment_names.append(f.name)
        else:
            mime_msg = MIMEText(body, "plain")
            mime_msg["to"]      = to
            mime_msg["subject"] = subject

        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()

        updated = service.users().drafts().update(
            userId="me",
            id=draft_id,
            body={"message": {"raw": raw}},
        ).execute()

        updated_draft_id = updated.get("id", draft_id)
        gmail_url        = f"https://mail.google.com/mail/#drafts/{updated_draft_id}"

        return Response({
            "mode":         "gmail_draft",
            "draft_id":     updated_draft_id,
            "gmail_url":    gmail_url,
            "to":           to,
            "subject":      subject,
            "body_preview": (body or "")[:200],
            "attachments":  attachment_names,
            "updated":      True,
            "next_step":    "Approve the task (or open in Gmail) to send the email.",
        })

    except Exception as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


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
# Document deliverable download
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def task_download(request, pk, filename):
    """
    GET /api/v1/tasks/<task_id>/download/<filename>/

    Streams a document deliverable created by the Document Agent.
    Returns 404 if the file doesn't exist or doesn't belong to this workspace.
    """
    import os
    from django.conf import settings
    from django.http import FileResponse, Http404

    workspace = _get_workspace(request)
    task = get_object_or_404(Task, id=pk, workspace=workspace)

    # Security: prevent path traversal
    safe_root = os.path.realpath(
        os.path.join(settings.MEDIA_ROOT, "deliverables", str(pk))
    )
    file_path  = os.path.realpath(os.path.join(safe_root, filename))
    if not file_path.startswith(safe_root):
        raise Http404("Invalid path.")
    if not os.path.exists(file_path):
        raise Http404("File not found. It may not have been created yet.")

    ext_map = {
        ".pdf":  "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    ext = os.path.splitext(filename)[1].lower()
    content_type = ext_map.get(ext, "application/octet-stream")

    return FileResponse(
        open(file_path, "rb"),
        as_attachment=True,
        filename=filename,
        content_type=content_type,
    )


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
