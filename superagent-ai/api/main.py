"""
Super Agent AI Platform — FastAPI application entry point.

Start the server:
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

Interactive API docs:
    http://localhost:8000/docs       (Swagger UI)
    http://localhost:8000/redoc      (ReDoc)

All routes require a valid JWT access token in the Authorization header:
    Authorization: Bearer <access_token>

Get a token first via POST /auth/login.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.auth_routes import router as auth_router
from api.routes.agent_routes import router as agent_router
from api.routes.email_routes import router as email_router
from api.routes.audit_routes import router as audit_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Super Agent AI Platform",
    description=(
        "AI-powered business automation API.\n\n"
        "**Authentication:** All endpoints (except `/auth/login`) require a "
        "JWT Bearer token.  Call `POST /auth/login` to get one.\n\n"
        "**Approval flow:**\n"
        "1. `POST /agent/run` — start a task.\n"
        "2. If response `status` is `pending_approval`, a YELLOW zone tool "
        "   (e.g. `send_email`) needs human review.\n"
        "3. `POST /agent/approve/{session_id}` — approve or deny it.\n"
        "4. On approval, the agent resumes automatically.\n\n"
        "**Never sends email without human approval.**"
    ),
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS — allow all origins in dev; restrict in production via env var
# ---------------------------------------------------------------------------

_ALLOWED_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth_router)
app.include_router(agent_router)
app.include_router(email_router)
app.include_router(audit_router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Health"], summary="Health check")
def health() -> dict:
    """Returns 200 OK if the server is running."""
    return {"status": "ok", "version": app.version}


# ---------------------------------------------------------------------------
# Startup log
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Super Agent API started — docs at http://localhost:8000/docs")
