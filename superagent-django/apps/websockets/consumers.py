import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class NotificationsConsumer(AsyncWebsocketConsumer):
    """
    WS channel: ws://host/ws/notifications/
    Each authenticated user gets their own group: notifications_<user_id>
    Receives: notification events pushed by the server.
    """

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        self.user_id = str(user.id)
        self.group_name = f"notifications_{self.user_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send(json.dumps({"type": "connected", "message": "Notifications connected"}))

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        # Client can send {"type": "ping"} to keep alive
        try:
            data = json.loads(text_data or "{}")
            if data.get("type") == "ping":
                await self.send(json.dumps({"type": "pong"}))
        except json.JSONDecodeError:
            pass

    # Handler for group_send messages
    async def notification_new(self, event):
        await self.send(json.dumps({
            "type": "notification",
            "data": event.get("data", {}),
        }))


class TaskLiveConsumer(AsyncWebsocketConsumer):
    """
    WS channel: ws://host/ws/tasks/<task_id>/
    Streams live step-by-step updates while a task is running.
    """

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        self.task_id = self.scope["url_route"]["kwargs"]["task_id"]
        self.group_name = f"task_{self.task_id}"

        # Verify task belongs to user's workspace
        if not await self._task_accessible(user, self.task_id):
            await self.close(code=4003)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send(json.dumps({"type": "connected", "task_id": self.task_id}))

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data or "{}")
            if data.get("type") == "ping":
                await self.send(json.dumps({"type": "pong"}))
        except json.JSONDecodeError:
            pass

    # Handler: receives from Celery task via group_send
    async def task_update(self, event):
        await self.send(json.dumps({
            "type": event.get("event", "update"),
            "data": event.get("data", {}),
        }))

    @staticmethod
    async def _task_accessible(user, task_id: str) -> bool:
        from channels.db import database_sync_to_async
        from apps.tasks.models import Task

        @database_sync_to_async
        def check():
            return Task.objects.filter(
                id=task_id,
                workspace__memberships__user=user
            ).exists()

        try:
            return await check()
        except Exception:
            return False


class AgentLiveConsumer(AsyncWebsocketConsumer):
    """
    WS channel: ws://host/ws/agents/<agent_id>/live/
    Streams live agent thought/action/observation events.
    """

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        self.agent_id = self.scope["url_route"]["kwargs"]["agent_id"]
        self.group_name = f"agent_live_{self.agent_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send(json.dumps({"type": "connected", "agent_id": self.agent_id}))

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data or "{}")
            if data.get("type") == "ping":
                await self.send(json.dumps({"type": "pong"}))
        except json.JSONDecodeError:
            pass

    # Handler for agent thought/action/observation events
    async def agent_event(self, event):
        await self.send(json.dumps({
            "type": event.get("event_type", "agent_event"),
            "data": event.get("data", {}),
        }))
