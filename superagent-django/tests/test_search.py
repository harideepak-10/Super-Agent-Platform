"""Tests for search endpoints."""
import pytest
from apps.agents.models import Agent
from apps.tasks.models import Task

pytestmark = pytest.mark.django_db


def make_agent(workspace, user, name="My Agent"):
    return Agent.objects.create(
        workspace=workspace, created_by=user,
        name=name, llm_model="llama-3.1-8b-instant",
        system_prompt="You are helpful.", tools=[],
    )


def make_task(workspace, user, prompt="Search for something"):
    return Task.objects.create(
        workspace=workspace, created_by=user,
        prompt=prompt, status=Task.Status.COMPLETED,
    )


class TestGlobalSearch:
    def test_search_returns_structure(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.get("/api/v1/search/?q=test")
        assert res.status_code == 200
        assert "tasks" in res.data
        assert "agents" in res.data
        assert "audit" in res.data

    def test_short_query_returns_empty(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.get("/api/v1/search/?q=a")
        assert res.status_code == 200
        assert res.data == {"tasks": [], "agents": [], "audit": []}

    def test_no_query_returns_empty(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.get("/api/v1/search/")
        assert res.status_code == 200

    def test_finds_agents(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_agent(ws, user, name="SuperBot Agent")
        res = client.get("/api/v1/search/?q=SuperBot")
        assert len(res.data["agents"]) == 1

    def test_finds_tasks(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_task(ws, user, prompt="Search for invoices")
        res = client.get("/api/v1/search/?q=invoices")
        assert len(res.data["tasks"]) == 1

    def test_requires_auth(self, api_client):
        res = api_client.get("/api/v1/search/?q=test")
        assert res.status_code == 401

    def test_workspace_isolation(self, create_user_with_workspace):
        user1, ws1, client1 = create_user_with_workspace("s1@k.tech")
        user2, ws2, client2 = create_user_with_workspace("s2@k.tech")
        make_agent(ws2, user2, name="OtherBot")
        res = client1.get("/api/v1/search/?q=OtherBot")
        assert res.data["agents"] == []


class TestSearchTasks:
    def test_list_all_tasks(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_task(ws, user)
        res = client.get("/api/v1/search/tasks/")
        assert res.status_code == 200
        assert len(res.data) >= 1

    def test_filter_by_query(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_task(ws, user, "find specific phrase here")
        res = client.get("/api/v1/search/tasks/?q=specific+phrase")
        assert len(res.data) == 1

    def test_filter_by_status(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_task(ws, user)
        res = client.get("/api/v1/search/tasks/?status=completed")
        assert all(t["status"] == "completed" for t in res.data)


class TestSearchAgents:
    def test_list_all_agents(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_agent(ws, user)
        res = client.get("/api/v1/search/agents/")
        assert res.status_code == 200
        assert len(res.data) >= 1

    def test_filter_by_name(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_agent(ws, user, "UniqueNameAgent")
        res = client.get("/api/v1/search/agents/?q=UniqueNameAgent")
        assert len(res.data) == 1

    def test_requires_auth(self, api_client):
        res = api_client.get("/api/v1/search/agents/")
        assert res.status_code == 401
