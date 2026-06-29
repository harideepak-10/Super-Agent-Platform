from django.urls import path
from .consumers import NotificationsConsumer, TaskLiveConsumer, AgentLiveConsumer

websocket_urlpatterns = [
    path("ws/notifications/", NotificationsConsumer.as_asgi()),
    path("ws/tasks/<uuid:task_id>/", TaskLiveConsumer.as_asgi()),
    path("ws/agents/<uuid:agent_id>/live/", AgentLiveConsumer.as_asgi()),
]
