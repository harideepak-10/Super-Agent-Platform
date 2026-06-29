"""Tests for notifications endpoints."""
import pytest
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db


def make_notification(user, workspace, title="Test", is_read=False):
    return Notification.objects.create(
        user=user, workspace=workspace,
        notification_type=Notification.NotificationType.TASK_COMPLETE,
        title=title, body="Something happened.",
        is_read=is_read,
    )


class TestNotificationList:
    def test_list_notifications(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_notification(user, ws)
        res = client.get("/api/v1/notifications/")
        assert res.status_code == 200
        assert len(res.data) == 1

    def test_filter_unread(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_notification(user, ws, is_read=True)
        make_notification(user, ws, is_read=False)
        res = client.get("/api/v1/notifications/?unread=true")
        assert all(n["is_read"] is False for n in res.data)

    def test_requires_auth(self, api_client):
        res = api_client.get("/api/v1/notifications/")
        assert res.status_code == 401


class TestNotificationDetail:
    def test_get_notification(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        notif = make_notification(user, ws)
        res = client.get(f"/api/v1/notifications/{notif.id}/")
        assert res.status_code == 200
        assert res.data["id"] == str(notif.id)

    def test_patch_marks_read(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        notif = make_notification(user, ws, is_read=False)
        res = client.patch(f"/api/v1/notifications/{notif.id}/")
        assert res.status_code == 200
        notif.refresh_from_db()
        assert notif.is_read is True

    def test_delete_notification(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        notif = make_notification(user, ws)
        res = client.delete(f"/api/v1/notifications/{notif.id}/")
        assert res.status_code == 204
        assert not Notification.objects.filter(id=notif.id).exists()


class TestMarkAllRead:
    def test_mark_all_read(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_notification(user, ws, is_read=False)
        make_notification(user, ws, is_read=False)
        res = client.post("/api/v1/notifications/mark-all-read/")
        assert res.status_code == 200
        assert Notification.objects.filter(user=user, is_read=False).count() == 0


class TestNotificationSettings:
    def test_get_settings(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.get("/api/v1/notifications/settings/")
        assert res.status_code == 200

    def test_patch_settings(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.patch("/api/v1/notifications/settings/", {
            "email_on_task_complete": False,
        })
        assert res.status_code == 200
