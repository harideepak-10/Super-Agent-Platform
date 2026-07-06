"""
Notification helper — call _notify() from anywhere to create a Notification row.
Import lazily inside functions to avoid circular imports.
"""


def _notify(user, workspace, notification_type, title, body="",
            resource_type="", resource_id="", metadata=None):
    """Create a Notification row. Silently ignores errors so it never breaks the caller."""
    if user is None or workspace is None:
        return
    try:
        from .models import Notification
        Notification.objects.create(
            user=user,
            workspace=workspace,
            notification_type=notification_type,
            title=title,
            body=body,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else "",
            metadata=metadata or {},
        )
    except Exception:
        import logging
        logging.getLogger("notifications").exception(
            "Failed to create notification type=%s user=%s", notification_type, user
        )


# ------------------------------------------------------------------
# Convenience wrappers
# ------------------------------------------------------------------

def notify_task_complete(task):
    if not task.created_by:
        return
    agent_name = task.agent.name if task.agent else "Agent"
    _notify(
        user=task.created_by,
        workspace=task.workspace,
        notification_type="task_complete",
        title=f"{agent_name} completed a task",
        body=task.result[:200] if task.result else task.prompt[:100],
        resource_type="task",
        resource_id=task.id,
        metadata={"agent_name": agent_name, "prompt": task.prompt[:100]},
    )


def notify_task_failed(task):
    if not task.created_by:
        return
    agent_name = task.agent.name if task.agent else "Agent"
    _notify(
        user=task.created_by,
        workspace=task.workspace,
        notification_type="task_failed",
        title=f"{agent_name} task failed",
        body=task.error_message[:200] if task.error_message else task.prompt[:100],
        resource_type="task",
        resource_id=task.id,
        metadata={"agent_name": agent_name, "prompt": task.prompt[:100]},
    )


def notify_approval_needed(task, approval):
    if not task.created_by:
        return
    agent_name = task.agent.name if task.agent else "Agent"
    _notify(
        user=task.created_by,
        workspace=task.workspace,
        notification_type="approval_needed",
        title=f"{agent_name} needs your approval",
        body=f"Action: {approval.tool_name} — {task.prompt[:80]}",
        resource_type="approval",
        resource_id=approval.id,
        metadata={"agent_name": agent_name, "tool_name": approval.tool_name},
    )


def notify_approval_decided(approval):
    """Notify the task creator that their approval was decided."""
    task = approval.task
    if not task.created_by:
        return
    agent_name = task.agent.name if task.agent else "Agent"
    decision = "approved" if approval.status == "approved" else "rejected"
    _notify(
        user=task.created_by,
        workspace=task.workspace,
        notification_type="approval_decided",
        title=f"Approval {decision} for {agent_name}",
        body=f"{approval.tool_name} was {decision}" + (
            f": {approval.reviewer_note}" if approval.reviewer_note else ""
        ),
        resource_type="approval",
        resource_id=approval.id,
        metadata={"decision": decision, "agent_name": agent_name, "tool_name": approval.tool_name},
    )


def notify_team_invite(invitation):
    """Notify the invited user that they have a pending invite."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    try:
        invitee = User.objects.get(email=invitation.email)
    except User.DoesNotExist:
        return
    inviter_name = invitation.invited_by.name if invitation.invited_by else "Someone"
    workspace_name = invitation.workspace.name if hasattr(invitation.workspace, "name") else "a workspace"
    _notify(
        user=invitee,
        workspace=invitation.workspace,
        notification_type="team_invite",
        title=f"{inviter_name} invited you to {workspace_name}",
        body=f"You have been invited as {invitation.role}. Accept or reject in Team > Invites.",
        resource_type="invitation",
        resource_id=invitation.id,
        metadata={"inviter": inviter_name, "role": invitation.role},
    )
