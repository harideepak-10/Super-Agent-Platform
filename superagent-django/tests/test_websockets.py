"""Tests for WebSocket consumers."""
import json
import uuid

import pytest
import pytest_asyncio
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

from channels.testing import WebsocketCommunicator
from apps.websockets.consumers import (
    AgentLiveConsumer,
    NotificationsConsumer,
    TaskLiveConsumer,
)

User = get_user_model()

pytestmark = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# Async fixture — creates user + workspace without touching sync DB from
# an async context directly.
# ---------------------------------------------------------------------------

@sync_to_async
def _make_user_ws(suffix=""):
    """Synchronous helper that creates user + workspace; wrapped via sync_to_async."""
    from apps.authentication.models import Workspace
    from apps.team.models import TeamMembership

    email = f"ws{suffix}_{uuid.uuid4().hex[:6]}@k.tech"
    user = User.objects.create_user(email=email, password="Test@1234", name="WS User")
    workspace = Workspace.objects.create(
        name=f"{email}'s Workspace",
        slug=email.split("@")[0].replace(".", "-").replace("_", "-"),
        owner=user,
    )
    TeamMembership.objects.create(
        workspace=workspace, user=user,
        role=TeamMembership.Role.OWNER,
    )
    return user, workspace


@sync_to_async
def _make_task(workspace, user, status_val=None):
    from apps.tasks.models import Task
    if status_val is None:
        status_val = Task.Status.RUNNING
    return Task.objects.create(
        workspace=workspace, created_by=user,
        prompt="live task test", status=status_val,
    )


# ---------------------------------------------------------------------------
# Communicator factories
# ---------------------------------------------------------------------------

def _notif_communicator(user):
    comm = WebsocketCommunicator(NotificationsConsumer.as_asgi(), "/ws/notifications/")
    comm.scope["user"] = user
    return comm


def _task_communicator(user, task_id):
    comm = WebsocketCommunicator(
        TaskLiveConsumer.as_asgi(), f"/ws/tasks/{task_id}/"
    )
    comm.scope["user"] = user
    comm.scope["url_route"] = {"kwargs": {"task_id": str(task_id)}}
    return comm


def _agent_communicator(user, agent_id):
    comm = WebsocketCommunicator(
        AgentLiveConsumer.as_asgi(), f"/ws/agents/{agent_id}/live/"
    )
    comm.scope["user"] = user
    comm.scope["url_route"] = {"kwargs": {"agent_id": str(agent_id)}}
    return comm


# ---------------------------------------------------------------------------
# NotificationsConsumer
# ---------------------------------------------------------------------------

class TestNotificationsConsumer:
    async def test_unauthenticated_rejected(self):
        """AnonymousUser is rejected with close code 4001."""
        comm = _notif_communicator(AnonymousUser())
        connected, code = await comm.connect()
        assert not connected
        assert code == 4001

    async def test_authenticated_accepts(self):
        """Valid user connects and receives 'connected' handshake message."""
        user, _ = await _make_user_ws("na")
        comm = _notif_communicator(user)
        connected, _ = await comm.connect()
        assert connected

        msg = json.loads(await comm.receive_from())
        assert msg["type"] == "connected"

        await comm.disconnect()

    async def test_ping_returns_pong(self):
        """Client sends ping, consumer replies pong."""
        user, _ = await _make_user_ws("np")
        comm = _notif_communicator(user)
        await comm.connect()
        await comm.receive_from()  # consume 'connected'

        await comm.send_to(json.dumps({"type": "ping"}))
        reply = json.loads(await comm.receive_from())
        assert reply["type"] == "pong"

        await comm.disconnect()

    async def test_receives_notification_broadcast(self):
        """Server-side group_send delivers notification to connected client."""
        from channels.layers import get_channel_layer

        user, _ = await _make_user_ws("nb")
        comm = _notif_communicator(user)
        await comm.connect()
        await comm.receive_from()  # consume 'connected'

        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"notifications_{user.id}",
            {
                "type": "notification_new",
                "data": {"title": "Task done", "body": "Your task finished."},
            },
        )

        msg = json.loads(await comm.receive_from())
        assert msg["type"] == "notification"
        assert msg["data"]["title"] == "Task done"

        await comm.disconnect()

    async def test_no_message_without_broadcast(self):
        """Consumer stays silent until a broadcast arrives."""
        user, _ = await _make_user_ws("ns")
        comm = _notif_communicator(user)
        await comm.connect()
        await comm.receive_from()  # consume 'connected'

        assert await comm.receive_nothing(timeout=0.1)

        await comm.disconnect()


# ---------------------------------------------------------------------------
# TaskLiveConsumer
# ---------------------------------------------------------------------------

class TestTaskLiveConsumer:
    async def test_unauthenticated_rejected(self):
        """AnonymousUser is rejected with 4001."""
        comm = _task_communicator(AnonymousUser(), uuid.uuid4())
        connected, code = await comm.connect()
        assert not connected
        assert code == 4001

    async def test_non_owned_task_rejected(self):
        """Task belonging to another workspace is rejected with 4003."""
        user1, _ = await _make_user_ws("tr1")
        user2, ws2 = await _make_user_ws("tr2")
        task = await _make_task(ws2, user2)

        comm = _task_communicator(user1, task.id)
        connected, code = await comm.connect()
        assert not connected
        assert code == 4003

    async def test_owned_task_accepted(self):
        """Task owner connects and gets 'connected' message with task_id."""
        user, ws = await _make_user_ws("ta")
        task = await _make_task(ws, user)

        comm = _task_communicator(user, task.id)
        connected, _ = await comm.connect()
        assert connected

        msg = json.loads(await comm.receive_from())
        assert msg["type"] == "connected"
        assert str(msg["task_id"]) == str(task.id)

        await comm.disconnect()

    async def test_ping_returns_pong(self):
        """Ping/pong works on task channel."""
        user, ws = await _make_user_ws("tp")
        task = await _make_task(ws, user)

        comm = _task_communicator(user, task.id)
        await comm.connect()
        await comm.receive_from()  # consume 'connected'

        await comm.send_to(json.dumps({"type": "ping"}))
        reply = json.loads(await comm.receive_from())
        assert reply["type"] == "pong"

        await comm.disconnect()

    async def test_receives_task_update_broadcast(self):
        """task_update group message is forwarded to connected client."""
        from channels.layers import get_channel_layer

        user, ws = await _make_user_ws("tu")
        task = await _make_task(ws, user)

        comm = _task_communicator(user, task.id)
        await comm.connect()
        await comm.receive_from()  # consume 'connected'

        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"task_{task.id}",
            {
                "type": "task_update",
                "event": "step_complete",
                "data": {"step": 1, "output": "Step done"},
            },
        )

        msg = json.loads(await comm.receive_from())
        assert msg["type"] == "step_complete"
        assert msg["data"]["step"] == 1

        await comm.disconnect()


# ---------------------------------------------------------------------------
# AgentLiveConsumer
# ---------------------------------------------------------------------------

class TestAgentLiveConsumer:
    async def test_unauthenticated_rejected(self):
        """AnonymousUser is rejected with 4001."""
        comm = _agent_communicator(AnonymousUser(), uuid.uuid4())
        connected, code = await comm.connect()
        assert not connected
        assert code == 4001

    async def test_authenticated_accepts(self):
        """Authenticated user connects and gets 'connected' message."""
        user, _ = await _make_user_ws("aa")
        agent_id = uuid.uuid4()
        comm = _agent_communicator(user, agent_id)
        connected, _ = await comm.connect()
        assert connected

        msg = json.loads(await comm.receive_from())
        assert msg["type"] == "connected"
        assert str(msg["agent_id"]) == str(agent_id)

        await comm.disconnect()

    async def test_ping_returns_pong(self):
        """Ping/pong works on agent live channel."""
        user, _ = await _make_user_ws("ap")
        comm = _agent_communicator(user, uuid.uuid4())
        await comm.connect()
        await comm.receive_from()  # consume 'connected'

        await comm.send_to(json.dumps({"type": "ping"}))
        reply = json.loads(await comm.receive_from())
        assert reply["type"] == "pong"

        await comm.disconnect()

    async def test_receives_agent_event_broadcast(self):
        """agent_event group message is forwarded to connected client."""
        from channels.layers import get_channel_layer

        user, _ = await _make_user_ws("ae")
        agent_id = uuid.uuid4()
        comm = _agent_communicator(user, agent_id)
        await comm.connect()
        await comm.receive_from()  # consume 'connected'

        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"agent_live_{agent_id}",
            {
                "type": "agent_event",
                "event_type": "thought",
                "data": {"content": "Thinking about the request..."},
            },
        )

        msg = json.loads(await comm.receive_from())
        assert msg["type"] == "thought"
        assert "content" in msg["data"]

        await comm.disconnect()

    async def test_multiple_clients_same_agent(self):
        """Two clients can both connect to the same agent live channel."""
        from channels.layers import get_channel_layer

        user, _ = await _make_user_ws("am")
        agent_id = uuid.uuid4()

        comm1 = _agent_communicator(user, agent_id)
        comm2 = _agent_communicator(user, agent_id)

        c1, _ = await comm1.connect()
        c2, _ = await comm2.connect()
        assert c1 and c2

        await comm1.receive_from()  # connected
        await comm2.receive_from()  # connected

        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"agent_live_{agent_id}",
            {"type": "agent_event", "event_type": "action", "data": {"tool": "search"}},
        )

        msg1 = json.loads(await comm1.receive_from())
        msg2 = json.loads(await comm2.receive_from())
        assert msg1["type"] == "action"
        assert msg2["type"] == "action"

        await comm1.disconnect()
        await comm2.disconnect()
