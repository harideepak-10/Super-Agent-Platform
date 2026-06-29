"""Celery tasks for running AI agents asynchronously."""
import logging
import sys
import os

from celery import shared_task
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ensure superagent-ai is on the Python path
# ---------------------------------------------------------------------------

_AI_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "superagent-ai")
if _AI_ROOT not in sys.path:
    sys.path.insert(0, os.path.abspath(_AI_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send_ws(channel_layer, group: str, event_type: str, data: dict):
    """Fire-and-forget WebSocket group send."""
    if channel_layer:
        try:
            async_to_sync(channel_layer.group_send)(
                group,
                {"type": "task.update", "event": event_type, "data": data},
            )
        except Exception:
            pass


def _notify_user(task, notif_type: str, message: str):
    """Create an in-app notification for the task owner."""
    try:
        from apps.notifications.models import Notification
        Notification.objects.create(
            user=task.created_by,
            workspace=task.workspace,
            notification_type=notif_type,
            title=message,
            resource_type="task",
            resource_id=str(task.id),
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# run_agent_task
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=0)
def run_agent_task(self, task_id: str):
    """Execute an agent task in a Celery worker."""
    from django.utils import timezone as dj_tz
    from apps.tasks.models import Task, TaskStep
    from apps.approvals.models import Approval
    from api.task_handler import TaskHandler, TaskRequest
    from api.result_handler import ResultHandler

    channel_layer = get_channel_layer()

    def ws(event_type, data):
        _send_ws(channel_layer, f"task_{task_id}", event_type, data)

    # --- Load task ---
    try:
        task = Task.objects.select_related("agent", "workspace", "created_by").get(id=task_id)
    except Task.DoesNotExist:
        logger.error("Task %s not found", task_id)
        return

    # --- Mark running ---
    task.status = Task.Status.RUNNING
    task.started_at = dj_tz.now()
    task.celery_task_id = self.request.id or ""
    task.save(update_fields=["status", "started_at", "celery_task_id"])
    ws("task_started", ResultHandler.ws_task_started(task_id))

    # --- Build request ---
    agent_config = task.agent
    req = TaskRequest(
        task_id=task_id,
        prompt=task.prompt,
        agent_type=agent_config.agent_type if agent_config else "auto",
        max_steps=agent_config.max_steps if agent_config else 20,
        max_cost=float(agent_config.max_cost_usd) if agent_config else 1.0,
    )

    # --- Execute ---
    result = TaskHandler().execute(req)

    if result.status == "completed":
        task.status = Task.Status.COMPLETED
        task.result = result.result
        task.completed_at = dj_tz.now()
        task.steps_taken = result.steps_taken
        task.cost_usd = result.cost_usd
        task.save(update_fields=["status", "result", "completed_at", "steps_taken", "cost_usd"])

        # Persist audit log as TaskStep rows
        for i, entry in enumerate(result.audit_log, start=1):
            TaskStep.objects.get_or_create(
                task=task,
                step_number=i,
                defaults={
                    "step_type": entry.get("event_type", "thought"),
                    "content": str(entry.get("details", "")),
                    "tool_name": entry.get("details", {}).get("tool_name", ""),
                    "tool_input": entry.get("details", {}).get("tool_input"),
                },
            )

        ws("task_completed", ResultHandler.ws_task_completed(
            task_id, result.result, result.steps_taken, result.cost_usd,
        ))
        _notify_user(task, "task_complete", f"Task completed: {task.prompt[:60]}...")

    elif result.status == "waiting_approval":
        task.status = Task.Status.WAITING_APPROVAL
        task.save(update_fields=["status"])

        payload = result.approval_payload or {}
        approval = Approval.objects.create(
            task=task,
            tool_name=payload.get("tool_name", ""),
            tool_input=payload.get("tool_input", ""),
            tool_zone="yellow",
            resume_snapshot=payload,
        )
        ws("approval_required", ResultHandler.ws_approval_required(
            task_id,
            str(approval.id),
            payload.get("tool_name", ""),
            payload.get("tool_input", ""),
        ))
        _notify_user(task, "approval_needed", f"Approval needed: {payload.get('tool_name', '')}")

    else:  # failed
        task.status = Task.Status.FAILED
        task.error_message = result.error
        task.completed_at = dj_tz.now()
        task.save(update_fields=["status", "error_message", "completed_at"])
        ws("task_failed", ResultHandler.ws_task_failed(task_id, result.error))
        _notify_user(task, "task_failed", f"Task failed: {result.error[:100]}")
        logger.error("Task %s failed: %s", task_id, result.error)


# ---------------------------------------------------------------------------
# resume_agent_task
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=0)
def resume_agent_task(self, task_id: str, approval_id: str, approved: bool, reviewer_note: str = ""):
    """Resume a paused task after a human approval decision."""
    from django.utils import timezone as dj_tz
    from apps.tasks.models import Task
    from apps.approvals.models import Approval
    from api.approval_handler import ApprovalHandler, ApprovalRequest
    from api.result_handler import ResultHandler

    channel_layer = get_channel_layer()

    def ws(event_type, data):
        _send_ws(channel_layer, f"task_{task_id}", event_type, data)

    # --- Load records ---
    try:
        task = Task.objects.select_related("agent", "workspace", "created_by").get(id=task_id)
        approval = Approval.objects.get(id=approval_id)
    except (Task.DoesNotExist, Approval.DoesNotExist):
        logger.error("Task or Approval not found: task=%s approval=%s", task_id, approval_id)
        return

    # --- Mark reviewed ---
    from django.utils import timezone as dj_tz
    approval.status = Approval.Status.APPROVED if approved else Approval.Status.REJECTED
    approval.reviewer_note = reviewer_note
    approval.reviewed_at = dj_tz.now()
    approval.save(update_fields=["status", "reviewer_note", "reviewed_at"])

    if not approved:
        task.status = Task.Status.CANCELLED
        task.error_message = f"Approval rejected: {reviewer_note or 'no reason given'}"
        task.completed_at = dj_tz.now()
        task.save(update_fields=["status", "error_message", "completed_at"])
        ws("task_cancelled", ResultHandler.ws_task_cancelled(task_id, reviewer_note))
        _notify_user(task, "task_cancelled", "Task was cancelled by reviewer")
        return

    # --- Resume ---
    agent_config = task.agent
    req = ApprovalRequest(
        task_id=task_id,
        approved=True,
        tool_name=approval.tool_name,
        tool_input=approval.tool_input,
        resume_snapshot=approval.resume_snapshot,
        original_prompt=task.prompt,
        agent_type=agent_config.agent_type if agent_config else "email",
        max_steps=agent_config.max_steps if agent_config else 20,
        max_cost=float(agent_config.max_cost_usd) if agent_config else 1.0,
        reviewer_note=reviewer_note,
    )

    task.status = Task.Status.RUNNING
    task.save(update_fields=["status"])
    ws("task_resumed", ResultHandler.ws_task_resumed(task_id))

    result = ApprovalHandler().resume(req)

    if result.status == "completed":
        task.status = Task.Status.COMPLETED
        task.result = result.result
        task.completed_at = dj_tz.now()
        task.steps_taken = result.steps_taken
        task.cost_usd = result.cost_usd
        task.save(update_fields=["status", "result", "completed_at", "steps_taken", "cost_usd"])
        ws("task_completed", ResultHandler.ws_task_completed(
            task_id, result.result, result.steps_taken, result.cost_usd,
        ))
        _notify_user(task, "task_complete", f"Task completed after approval: {task.prompt[:60]}...")

    elif result.status == "waiting_approval":
        # Another YELLOW tool hit — create another Approval record
        from apps.approvals.models import Approval as Appr
        task.status = Task.Status.WAITING_APPROVAL
        task.save(update_fields=["status"])

        payload = result.approval_payload or {}
        new_approval = Appr.objects.create(
            task=task,
            tool_name=payload.get("tool_name", ""),
            tool_input=payload.get("tool_input", ""),
            tool_zone="yellow",
            resume_snapshot=payload,
        )
        ws("approval_required", ResultHandler.ws_approval_required(
            task_id,
            str(new_approval.id),
            payload.get("tool_name", ""),
            payload.get("tool_input", ""),
        ))
        _notify_user(task, "approval_needed", f"Another approval needed: {payload.get('tool_name', '')}")

    else:  # failed / rejected
        task.status = Task.Status.FAILED
        task.error_message = result.error
        task.completed_at = dj_tz.now()
        task.save(update_fields=["status", "error_message", "completed_at"])
        ws("task_failed", ResultHandler.ws_task_failed(task_id, result.error))
        _notify_user(task, "task_failed", f"Task failed after approval: {result.error[:100]}")
        logger.error("Resumed task %s failed: %s", task_id, result.error)
