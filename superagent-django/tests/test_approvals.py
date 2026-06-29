"""Tests for approvals endpoints."""
import pytest
from apps.approvals.models import Approval, ApprovalRule
from apps.tasks.models import Task

pytestmark = pytest.mark.django_db


def make_task(ws, user):
    return Task.objects.create(
        workspace=ws, created_by=user,
        prompt="Test task", status=Task.Status.WAITING_APPROVAL,
    )


def make_approval(task, tool_name="web_search"):
    return Approval.objects.create(
        task=task,
        tool_name=tool_name,
        tool_input={"query": "test"},
        status=Approval.Status.PENDING,
    )


class TestApprovalList:
    def test_list_returns_approvals(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        task = make_task(ws, user)
        make_approval(task)
        res = client.get("/api/v1/approvals/")
        assert res.status_code == 200
        assert len(res.data) >= 1

    def test_list_requires_auth(self, api_client):
        res = api_client.get("/api/v1/approvals/")
        assert res.status_code == 401

    def test_filter_by_status(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        task = make_task(ws, user)
        make_approval(task)
        res = client.get("/api/v1/approvals/?status=pending")
        assert all(a["status"] == "pending" for a in res.data)


class TestPendingApprovals:
    def test_pending_only_returns_pending(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        task = make_task(ws, user)
        make_approval(task)
        res = client.get("/api/v1/approvals/pending/")
        assert res.status_code == 200
        assert all(a["status"] == "pending" for a in res.data)


class TestApprovalDetail:
    def test_get_approval(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        task = make_task(ws, user)
        approval = make_approval(task)
        res = client.get(f"/api/v1/approvals/{approval.id}/")
        assert res.status_code == 200
        assert res.data["id"] == str(approval.id)


class TestApprovalDecide:
    def test_approve(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        task = make_task(ws, user)
        approval = make_approval(task)
        res = client.post(f"/api/v1/approvals/{approval.id}/decide/", {
            "approved": True, "note": "Looks good"
        })
        assert res.status_code == 200
        assert res.data["status"] == "approved"

    def test_reject(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        task = make_task(ws, user)
        approval = make_approval(task)
        res = client.post(f"/api/v1/approvals/{approval.id}/decide/", {
            "approved": False, "note": "Too risky"
        })
        assert res.status_code == 200
        assert res.data["status"] == "rejected"

    def test_decide_already_decided_returns_404(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        task = make_task(ws, user)
        approval = make_approval(task)
        approval.status = Approval.Status.APPROVED
        approval.save()
        res = client.post(f"/api/v1/approvals/{approval.id}/decide/", {
            "approved": True, "note": ""
        })
        assert res.status_code == 404


class TestApprovalRules:
    def test_list_rules(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.get("/api/v1/approvals/rules/")
        assert res.status_code == 200

    def test_create_rule(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.post("/api/v1/approvals/rules/", {
            "tool_name": "file_write",
            "always_require": True,
        })
        assert res.status_code == 201

    def test_update_rule(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        rule = ApprovalRule.objects.create(
            workspace=ws, created_by=user,
            tool_name="file_write", always_require=True,
        )
        res = client.patch(f"/api/v1/approvals/rules/{rule.id}/", {"always_block": True})
        assert res.status_code == 200

    def test_delete_rule(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        rule = ApprovalRule.objects.create(
            workspace=ws, created_by=user,
            tool_name="file_write", always_require=True,
        )
        res = client.delete(f"/api/v1/approvals/rules/{rule.id}/")
        assert res.status_code == 204
