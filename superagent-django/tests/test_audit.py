"""Tests for audit log endpoints."""
import pytest
from apps.audit.models import AuditEvent
from apps.audit.utils import log_event

pytestmark = pytest.mark.django_db


def make_event(workspace, user, event_type="agent_created", resource_type="agent", resource_id="abc"):
    return AuditEvent.objects.create(
        workspace=workspace, actor=user,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
    )


class TestAuditList:
    def test_list_events(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_event(ws, user)
        res = client.get("/api/v1/audit/")
        assert res.status_code == 200
        assert len(res.data) >= 1

    def test_list_requires_auth(self, api_client):
        res = api_client.get("/api/v1/audit/")
        assert res.status_code == 401

    def test_filter_by_event_type(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_event(ws, user, event_type="agent_created")
        make_event(ws, user, event_type="task_created")
        res = client.get("/api/v1/audit/?event_type=agent_created")
        assert all(e["event_type"] == "agent_created" for e in res.data)

    def test_filter_by_resource_type(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_event(ws, user, resource_type="agent")
        make_event(ws, user, resource_type="task")
        res = client.get("/api/v1/audit/?resource_type=agent")
        assert all(e["resource_type"] == "agent" for e in res.data)

    def test_workspace_isolation(self, create_user_with_workspace):
        user1, ws1, client1 = create_user_with_workspace("al1@k.tech")
        user2, ws2, client2 = create_user_with_workspace("al2@k.tech")
        make_event(ws2, user2)
        res = client1.get("/api/v1/audit/")
        assert res.data == []


class TestAuditSummary:
    def test_summary_returns_counts(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_event(ws, user, "agent_created")
        make_event(ws, user, "agent_created")
        make_event(ws, user, "task_created")
        res = client.get("/api/v1/audit/summary/")
        assert res.status_code == 200
        counts = {item["event_type"]: item["count"] for item in res.data}
        assert counts.get("agent_created") == 2
        assert counts.get("task_created") == 1


class TestAuditEventTypes:
    def test_event_types_list(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.get("/api/v1/audit/event-types/")
        assert res.status_code == 200
        assert isinstance(res.data, list)
        assert len(res.data) > 0
        assert "value" in res.data[0]
        assert "label" in res.data[0]


class TestAuditByResource:
    def test_by_resource(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_event(ws, user, resource_type="agent", resource_id="abc123")
        res = client.get("/api/v1/audit/resource/agent/abc123/")
        assert res.status_code == 200
        assert len(res.data) >= 1


class TestAuditByActor:
    def test_by_actor(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_event(ws, user)
        res = client.get(f"/api/v1/audit/actor/{user.id}/")
        assert res.status_code == 200
        assert len(res.data) >= 1
