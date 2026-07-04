"""Tests for costs endpoints."""
import pytest
from django.utils import timezone
from apps.costs.models import DailyCost, Budget

pytestmark = pytest.mark.django_db


def make_daily_cost(workspace, cost=1.50, tokens=5000, tasks=3):
    return DailyCost.objects.create(
        workspace=workspace,
        date=timezone.now().date(),
        total_cost_usd=cost,
        total_tokens=tokens,
        task_count=tasks,
    )


class TestCostSummary:
    def test_summary_returns_structure(self, create_user_with_workspace):
        _, ws, client = create_user_with_workspace()
        make_daily_cost(ws)
        res = client.get("/api/v1/costs/summary/")
        assert res.status_code == 200
        assert "monthly" in res.data
        assert "today" in res.data
        assert "budget" in res.data

    def test_summary_no_data_returns_zeros(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.get("/api/v1/costs/summary/")
        assert res.status_code == 200
        assert res.data["monthly"]["total_cost_eur"] == 0

    def test_summary_requires_auth(self, api_client):
        res = api_client.get("/api/v1/costs/summary/")
        assert res.status_code == 401


class TestCostDaily:
    def test_daily_returns_list(self, create_user_with_workspace):
        _, ws, client = create_user_with_workspace()
        make_daily_cost(ws)
        res = client.get("/api/v1/costs/daily/")
        assert res.status_code == 200
        assert isinstance(res.data, list)

    def test_daily_respects_days_param(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.get("/api/v1/costs/daily/?days=7")
        assert res.status_code == 200


class TestCostByAgent:
    def test_by_agent_empty(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.get("/api/v1/costs/by-agent/")
        assert res.status_code == 200
        assert isinstance(res.data, list)


class TestBudget:
    def test_get_budgets(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.get("/api/v1/costs/budget/")
        assert res.status_code == 200

    def test_create_budget(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.post("/api/v1/costs/budget/", {
            "period": "monthly",
            "limit_usd": "100.00",
        })
        assert res.status_code in (200, 201)
        assert Budget.objects.filter(period="monthly").exists()

    def test_update_budget_upserts(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        client.post("/api/v1/costs/budget/", {"period": "monthly", "limit_usd": "50.00"})
        res = client.post("/api/v1/costs/budget/", {"period": "monthly", "limit_usd": "200.00"})
        assert res.status_code == 200
        assert Budget.objects.filter(period="monthly").count() == 1

    def test_budget_requires_auth(self, api_client):
        res = api_client.get("/api/v1/costs/budget/")
        assert res.status_code == 401
