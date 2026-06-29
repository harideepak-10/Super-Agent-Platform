"""Tests for tasks endpoints."""
import pytest
from apps.tasks.models import Task
from apps.agents.models import Agent

pytestmark = pytest.mark.django_db


def make_agent(workspace, user):
    return Agent.objects.create(
        workspace=workspace, created_by=user,
        name="Test Agent", llm_model="llama-3.1-8b-instant",
        system_prompt="You are helpful.", tools=[],
    )


def make_task(workspace, user, agent=None, status=Task.Status.COMPLETED):
    return Task.objects.create(
        workspace=workspace, created_by=user,
        agent=agent, prompt="Do something useful.",
        status=status, result="Done.",
    )


class TestTaskList:
    def test_list_returns_tasks(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_task(ws, user)
        res = client.get("/api/v1/tasks/")
        assert res.status_code == 200
        assert len(res.data) >= 1

    def test_list_requires_auth(self, api_client):
        res = api_client.get("/api/v1/tasks/")
        assert res.status_code == 401

    def test_list_filters_by_status(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_task(ws, user, status=Task.Status.COMPLETED)
        make_task(ws, user, status=Task.Status.FAILED)
        res = client.get("/api/v1/tasks/?status=completed")
        assert all(t["status"] == "completed" for t in res.data)

    def test_workspace_isolation(self, create_user_with_workspace):
        user1, ws1, client1 = create_user_with_workspace("t1@k.tech")
        user2, ws2, client2 = create_user_with_workspace("t2@k.tech")
        make_task(ws2, user2)
        res = client1.get("/api/v1/tasks/")
        assert res.data == []


class TestTaskCreate:
    def test_create_without_agent(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.post("/api/v1/tasks/create/", {"prompt": "Hello world"})
        assert res.status_code == 201
        assert res.data["prompt"] == "Hello world"

    def test_create_with_agent(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        agent = make_agent(ws, user)
        res = client.post("/api/v1/tasks/create/", {
            "prompt": "Test with agent",
            "agent_id": str(agent.id),
        })
        assert res.status_code == 201

    def test_create_requires_prompt(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.post("/api/v1/tasks/create/", {})
        assert res.status_code == 400

    def test_create_requires_auth(self, api_client):
        res = api_client.post("/api/v1/tasks/create/", {"prompt": "test"})
        assert res.status_code == 401


class TestTaskDetail:
    def test_get_task(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        task = make_task(ws, user)
        res = client.get(f"/api/v1/tasks/{task.id}/")
        assert res.status_code == 200
        assert res.data["id"] == str(task.id)

    def test_other_workspace_returns_404(self, create_user_with_workspace):
        user1, ws1, client1 = create_user_with_workspace("td1@k.tech")
        user2, ws2, client2 = create_user_with_workspace("td2@k.tech")
        task = make_task(ws2, user2)
        res = client1.get(f"/api/v1/tasks/{task.id}/")
        assert res.status_code == 404


class TestTaskResult:
    def test_get_result(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        task = make_task(ws, user, status=Task.Status.COMPLETED)
        res = client.get(f"/api/v1/tasks/{task.id}/result/")
        assert res.status_code == 200
        assert "result" in res.data
        assert "status" in res.data


class TestTaskSteps:
    def test_steps_empty(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        task = make_task(ws, user)
        res = client.get(f"/api/v1/tasks/{task.id}/steps/")
        assert res.status_code == 200
        assert res.data == []


class TestTaskCancel:
    def test_cancel_queued_task(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        task = make_task(ws, user, status=Task.Status.QUEUED)
        res = client.post(f"/api/v1/tasks/{task.id}/cancel/")
        assert res.status_code == 200
        task.refresh_from_db()
        assert task.status == Task.Status.CANCELLED

    def test_cancel_already_completed(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        task = make_task(ws, user, status=Task.Status.COMPLETED)
        res = client.post(f"/api/v1/tasks/{task.id}/cancel/")
        assert res.status_code == 400


class TestTaskRetry:
    def test_retry_failed_task(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        task = make_task(ws, user, status=Task.Status.FAILED)
        res = client.post(f"/api/v1/tasks/{task.id}/retry/")
        assert res.status_code == 201

    def test_retry_completed_task_fails(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        task = make_task(ws, user, status=Task.Status.COMPLETED)
        res = client.post(f"/api/v1/tasks/{task.id}/retry/")
        assert res.status_code == 400
