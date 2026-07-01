"""
JWT authentication middleware for Django Channels WebSockets.

Reads the token from:
  1. Query string: ?token=<jwt>
  2. Authorization header: Bearer <jwt>
"""
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser


@database_sync_to_async
def _get_user_from_token(token: str):
    try:
        from rest_framework_simplejwt.tokens import AccessToken
        from django.contrib.auth import get_user_model
        User = get_user_model()
        validated = AccessToken(token)
        return User.objects.get(id=validated["user_id"])
    except Exception:
        return AnonymousUser()


class JWTAuthMiddleware:
    """Authenticate WebSocket connections via JWT."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Try query string first: ws://.../?token=xxx
        query_string = scope.get("query_string", b"").decode()
        params = parse_qs(query_string)
        token = None

        if "token" in params:
            token = params["token"][0]
        else:
            # Try Authorization header
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode()
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]

        if token:
            scope["user"] = await _get_user_from_token(token)
        else:
            scope["user"] = AnonymousUser()

        return await self.app(scope, receive, send)
