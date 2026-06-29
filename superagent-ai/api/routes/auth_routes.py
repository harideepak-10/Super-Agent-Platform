"""
Authentication routes.

POST /auth/login   — exchange username+password for a JWT token pair
POST /auth/refresh — exchange a refresh token for a new access token
GET  /auth/me      — verify the current token and return user info
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import (
    authenticate_user,
    create_access_token,
    create_refresh_token,
    get_current_user,
    verify_token,
)
from api.models import LoginRequest, RefreshRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and obtain JWT tokens",
    description=(
        "Submit your username and password to receive an access token "
        "(short-lived) and a refresh token (long-lived).  "
        "Include the access token as `Authorization: Bearer <token>` "
        "on all subsequent requests."
    ),
)
def login(request: LoginRequest) -> TokenResponse:
    """Authenticate and return a token pair.

    Args:
        request: Username and password.

    Returns:
        JWT access token and refresh token.

    Raises:
        HTTPException 401: If credentials are incorrect.
    """
    if not authenticate_user(request.username, request.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_data = {"sub": request.username}
    access_token, expires_in = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
    description=(
        "Submit a valid refresh token to obtain a new access token "
        "without re-entering your credentials."
    ),
)
def refresh(request: RefreshRequest) -> TokenResponse:
    """Issue a new access token from a valid refresh token.

    Args:
        request: The refresh token.

    Returns:
        New JWT access token (and the same refresh token).

    Raises:
        HTTPException 401: If the refresh token is invalid or expired.
    """
    payload = verify_token(request.refresh_token, expected_type="refresh")
    token_data = {"sub": payload["sub"]}
    access_token, expires_in = create_access_token(token_data)
    new_refresh = create_refresh_token(token_data)

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh,
        expires_in=expires_in,
    )


@router.get(
    "/me",
    summary="Get current user info",
    description="Returns the username encoded in the current access token.",
)
def me(current_user: dict[str, Any] = Depends(get_current_user)) -> dict:
    """Return the authenticated user's identity.

    Args:
        current_user: Injected JWT payload from the auth dependency.

    Returns:
        Dict with ``username``.
    """
    return {"username": current_user.get("sub")}
