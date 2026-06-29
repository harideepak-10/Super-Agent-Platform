"""Tests for agents CRUD endpoints."""
import pytest
from apps.agents.models import Agent

pytestmark = pytest.mark.django_db


def make_agent(workspace, user, name="Test Agent"):
    return Agent.objects.create(
        workspace=workspace,
        created_by=user,
        name=name,
        description="A test agent",
        llm_model="llama-3.1-8b-instant",
        system_prompt="You are a test agent.",
        tools=[],
    )


class TestAgentList:
    def test_list_returns_agents(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_agent(ws, user)
        res = client.get("/api/v1/agents/")
        assert res.status_code == 200
        assert len(res.data) == 1

    def test_list_requires_auth(self, api_client):
        res = api_client.get("/api/v1/agents/")
        assert res.status_code == 401

    def test_list_only_returns_own_workspace_agents(self, create_user_with_workspace):
        user1, ws1, client1 = create_user_with_workspace("u1@k.tech")
        user2, ws2, client2 = create_user_with_workspace("u2@k.tech")
        make_agent(ws1, user1, "Agent1")
        make_agent(ws2, user2, "Agent2")
        res = client1.get("/api/v1/agents/")
        names = [a["name"] for a in res.data]
        assert "Agent1" in names
        assert "Agent2" not in names


class TestAgentCreate:
    def test_create_agent_success(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.post("/api/v1/agents/create/", {
            "name": "My Agent",
            "description": "Does stuff",
            "llm_model": "llama-3.1-8b-instant",
            "system_prompt": "You are helpful.",
        })
        assert res.status_code == 201
        assert res.data["name"] == "My Agent"

    def test_create_requires_name(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.post("/api/v1/agents/create/", {"description": "no name"})
        assert res.status_code == 400

    def test_create_requires_auth(self, api_client):
        res = api_client.post("/api/v1/agents/create/", {"name": "X"})
        assert res.status_code == 401


class TestAgentDetail:
    def test_get_agent(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        agent = make_agent(ws, user)
        res = client.get(f"/api/v1/agents/{agent.id}/")
        assert res.status_code == 200
        assert res.data["id"] == str(agent.id)

    def test_get_other_workspace_returns_404(self, create_user_with_workspace):
        user1, ws1, client1 = create_user_with_workspace("o1@k.tech")
        user2, ws2, client2 = create_user_with_workspace("o2@k.tech")
        agent = make_agent(ws2, user2)
        res = client1.get(f"/api/v1/agents/{agent.id}/")
        assert res.status_code == 404


class TestAgentUpdate:
    def test_patch_agent(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        agent = make_agent(ws, user)
        res = client.patch(f"/api/v1/agents/{agent.id}/update/", {"name": "Updated"})
        assert res.status_code == 200
        assert res.data["name"] == "Updated"


class TestAgentDelete:
    def test_delete_soft_deletes(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        agent = make_agent(ws, user)
        res = client.delete(f"/api/v1/agents/{agent.id}/delete/")
        assert res.status_code == 204
        agent.refresh_from_db()
        assert agent.is_active is False

    def test_deleted_agent_not_in_list(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        agent = make_agent(ws, user)
        client.delete(f"/api/v1/agents/{agent.id}/delete/")
        res = client.get("/api/v1/agents/")
        assert all(a["id"] != str(agent.id) for a in res.data)


class TestAgentTasks:
    def test_agent_tasks_empty(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        agent = make_agent(ws, user)
        res = client.get(f"/api/v1/agents/{agent.id}/tasks/")
        assert res.status_code == 200
        assert res.data == []
