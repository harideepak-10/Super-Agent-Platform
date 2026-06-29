"""
JWT authentication for the Super Agent API.

Provides:
  - create_access_token / create_refresh_token   — issue signed JWTs
  - verify_token                                  — decode and validate
  - get_current_user                              — FastAPI dependency
  - verify_password / authenticate_user           — credential check

Configuration via environment variables:
    JWT_SECRET_KEY                  — REQUIRED, min 32 chars
    JWT_ALGORITHM                   — default HS256
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES — default 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS   — default 7
    API_USERNAME                    — default admin
    API_PASSWORD_HASH               — bcrypt hash of the password

To generate a password hash:
    python -c "from passlib.hash import bcrypt; print(bcrypt.hash('yourpassword'))"
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Lazy imports so the module loads even when packages aren't installed
# (relevant for tests that import other modules without running the API)
try:
    from jose import JWTError, jwt  # type: ignore[import]
    from passlib.hash import bcrypt  # type: ignore[import]
    _JWT_AVAILABLE = True
except ImportError:
    _JWT_AVAILABLE = False


_bearer_scheme = HTTPBearer()

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _require_jwt() -> None:
    if not _JWT_AVAILABLE:
        raise ImportError(
            "python-jose and passlib are required for JWT auth. "
            "Run: pip install python-jose[cryptography] passlib[bcrypt]"
        )


def _secret_key() -> str:
    key = os.environ.get("JWT_SECRET_KEY", "")
    if not key:
        raise EnvironmentError(
            "JWT_SECRET_KEY environment variable is not set. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    if len(key) < 32:
        raise ValueError("JWT_SECRET_KEY must be at least 32 characters long.")
    return key


def _algorithm() -> str:
    return os.environ.get("JWT_ALGORITHM", "HS256")


def _access_expire_minutes() -> int:
    return int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30"))


def _refresh_expire_days() -> int:
    return int(os.environ.get("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))


def _api_username() -> str:
    return os.environ.get("API_USERNAME", "admin")


def _api_password_hash() -> str:
    h = os.environ.get("API_PASSWORD_HASH", "")
    if not h:
        raise EnvironmentError(
            "API_PASSWORD_HASH environment variable is not set. "
            "Generate with: python -c \"from passlib.hash import bcrypt; print(bcrypt.hash('yourpassword'))\""
        )
    return h


# ---------------------------------------------------------------------------
# Password verification
# ---------------------------------------------------------------------------


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain-text password against its bcrypt hash.

    Args:
        plain_password:  The password provided by the user.
        hashed_password: The stored bcrypt hash.

    Returns:
        True if the password matches, False otherwise.
    """
    _require_jwt()
    return bcrypt.verify(plain_password, hashed_password)


def authenticate_user(username: str, password: str) -> bool:
    """Check username and password against configured credentials.

    Args:
        username: Submitted username.
        password: Submitted plain-text password.

    Returns:
        True if credentials are valid.
    """
    if username != _api_username():
        return False
    return verify_password(password, _api_password_hash())


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------


def create_access_token(data: dict[str, Any]) -> tuple[str, int]:
    """Create a short-lived JWT access token.

    Args:
        data: Claims to encode (must include ``"sub"`` for the subject).

    Returns:
        Tuple of (encoded_jwt: str, expires_in_seconds: int).
    """
    _require_jwt()
    expire_minutes = _access_expire_minutes()
    expire = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
    payload = {**data, "exp": expire, "type": "access"}
    token = jwt.encode(payload, _secret_key(), algorithm=_algorithm())
    return token, expire_minutes * 60


def create_refresh_token(data: dict[str, Any]) -> str:
    """Create a long-lived JWT refresh token.

    Args:
        data: Claims to encode (must include ``"sub"``).

    Returns:
        Encoded JWT string.
    """
    _require_jwt()
    expire = datetime.now(timezone.utc) + timedelta(days=_refresh_expire_days())
    payload = {**data, "exp": expire, "type": "refresh"}
    return jwt.encode(payload, _secret_key(), algorithm=_algorithm())


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def verify_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    """Decode and validate a JWT token.

    Args:
        token:         The encoded JWT string.
        expected_type: ``"access"`` or ``"refresh"``.

    Returns:
        The decoded payload dict.

    Raises:
        HTTPException 401: If the token is invalid, expired, or wrong type.
    """
    _require_jwt()
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=[_algorithm()])
        if payload.get("type") != expected_type:
            raise credentials_exception
        if payload.get("sub") is None:
            raise credentials_exception
        return payload
    except JWTError:
        raise credentials_exception


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict[str, Any]:
    """FastAPI dependency that validates the Bearer token on every request.

    Usage::

        @router.get("/protected")
        def protected(user: dict = Depends(get_current_user)):
            return {"hello": user["sub"]}

    Args:
        credentials: Injected by FastAPI from the Authorization header.

    Returns:
        The decoded JWT payload.

    Raises:
        HTTPException 401: If the token is missing or invalid.
    """
    return verify_token(credentials.credentials, expected_type="access")
