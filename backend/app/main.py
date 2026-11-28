"""
FastAPI Application Entry Point — main.py

Serves:
- REST API on /api/v1/*
- WebSocket for customer chat on /ws/chat/{session_id}
- WebSocket for agent dashboard on /ws/agent/{session_id}
- Health check on /health
"""

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit

from langsmith import utils as langsmith_utils

from fastapi import FastAPI, WebSocket, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Route

from app.config import settings
from app.mcp.http_server import StaffMCP, mcp_http
from app.routes import auth_router, router as api_router
from app.websocket.chat_handler import handle_customer_ws, handle_agent_ws
from app.persistence.transcript import transcript_persistence

from app.logger import setup_logger, set_trace_id
from app.dependencies import verify_agent_ws_token

# ──────────────────────────────────────────────
#  Logging
# ──────────────────────────────────────────────
setup_logger(log_level=settings.log_level)

# Query-string authentication is retained for browser WebSockets, so prevent
# bearer tokens and static credentials from leaking into access/error logs.
from app.logging_security import QuerySecretRedactionFilter
_query_secret_filter = QuerySecretRedactionFilter()
for _logger_name in ("uvicorn.access", "uvicorn.error", "customer_support", "tracking"):
    logging.getLogger(_logger_name).addFilter(_query_secret_filter)

logger = logging.getLogger("customer_support")


# ──────────────────────────────────────────────
#  Request ID Middleware
# ──────────────────────────────────────────────
class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique X-Request-ID to every request for tracing."""
    # Every HTTP request gets a unique 12-char hex ID for log correlation

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        set_trace_id(request_id)

        # Log the request
        logger.debug(
            f"{request.method} {request.url.path}",
            extra={"request_id": request_id, "client": request.client.host if request.client else "unknown"},
        )

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ──────────────────────────────────────────────
#  Startup Health Checks
# ──────────────────────────────────────────────
def _run_startup_checks():
    """Validate that critical services are reachable at boot time."""
    # Non-blocking: logs warnings but doesn't prevent startup
    import urllib.request
    import json as _json

    checks = {}

    from app.tools import check_llm_health
    checks["llm"] = check_llm_health()["status"]

    # Check Supabase DB connectivity
    try:
        from supabase import create_client
        sb = create_client(settings.supabase_url, settings.supabase_key)
        sb.table("customers").select("id").limit(1).execute()
        checks["supabase"] = "OK"
    except Exception as e:
        checks["supabase"] = f"UNREACHABLE ({e})"
        logger.warning(f"Supabase not reachable at startup: {e}")

    # Check Pinecone vector DB connectivity
    try:
        from pinecone import Pinecone
        pc = Pinecone(api_key=settings.pinecone_api_key)
        index = pc.Index(settings.pinecone_index_name)
        stats = index.describe_index_stats()
        checks["pinecone"] = f"OK"
    except Exception as e:
        checks["pinecone"] = f"UNREACHABLE ({e})"
        logger.warning(f"Pinecone not reachable at startup: {e}")

    for service, status in checks.items():
        logger.info(f"Startup check — {service}: {status}")


# ──────────────────────────────────────────────
#  App Lifecycle
# ──────────────────────────────────────────────
def _log_tracing_status() -> None:
    # Log only the origin. An endpoint may include credentials or query parameters.
    try:
        endpoint = urlsplit(langsmith_utils.get_api_url(None))
        origin = f"{endpoint.scheme}://{endpoint.hostname}"
        if endpoint.port:
            origin += f":{endpoint.port}"
    except ValueError:
        origin = "invalid"
    logger.info(
        "LangSmith tracing %s project=%s endpoint=%s api_key_set=%s",
        "enabled" if langsmith_utils.tracing_is_enabled() else "disabled",
        langsmith_utils.get_tracer_project(),
        origin,
        bool(langsmith_utils.get_env_var("API_KEY")),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.llm_factory import resolve_model

    logger.info("=== Customer Support AI Server Starting ===")
    _log_tracing_status()
    logger.info("LLM Provider  : %s", settings.llm_provider)
    logger.info(f"LLM Model     : {resolve_model()}")
    if settings.llm_provider == "bedrock":
        logger.info("AWS Region    : %s", settings.aws_region)
    else:
        logger.info("GCP Project   : %s", settings.vertex_project_id)
        logger.info("GCP Location  : %s", settings.vertex_location)
    logger.info(f"Supabase URL  : {settings.supabase_url}")
    logger.info(f"Log Level     : {settings.log_level}")
    logger.info(f"CORS Origins  : {settings.cors_origins_list}")

    workers = os.getenv("WEB_CONCURRENCY", "1")
    if workers.isdecimal() and int(workers) > 1:
        logger.critical("WEB_CONCURRENCY=%s: WebSocket presence and delivery require one API worker and replica", workers)

    try:
        await asyncio.to_thread(_run_startup_checks)
    except Exception as e:
        logger.error(f"Startup checks failed: {e}")

    # Start the durable outbox worker + reconciliation scheduler
    try:
        from app.outbox.lifecycle import start_outbox_worker, start_reconciliation_scheduler
        await start_outbox_worker()
        await start_reconciliation_scheduler()
    except Exception as e:
        logger.error(f"Outbox worker failed to start: {e}")

    try:
        async with mcp_http.lifespan(mcp_http), transcript_persistence():
            yield
    finally:
        try:
            from app.outbox.lifecycle import stop_outbox_worker, stop_reconciliation_scheduler
            await stop_reconciliation_scheduler()
            await stop_outbox_worker()
        except Exception as e:
            logger.error(f"Outbox worker failed to stop cleanly: {e}")

        logger.info("=== Customer Support AI Server Stopped ===")


# ──────────────────────────────────────────────
#  FastAPI App
# ──────────────────────────────────────────────
app = FastAPI(
    title="Intelligent Customer Support System",
    description="Multi-agent customer support powered by LangGraph, MCP, and RAG.",
    version="2.0.0",
    lifespan=lifespan,
)

# Request ID middleware (must come before CORS)
app.add_middleware(RequestIDMiddleware)

# Rate limiting must run inside CORS so browsers can read 429 responses.
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    application_limits=["300/minute"],
    storage_uri=settings.http_rate_limit_storage_uri or "memory://",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# ──────────────────────────────────────────────
#  Global Exception Handler
# ──────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions and return a clean JSON error + log it."""
    from fastapi import HTTPException
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": "http_exception", "message": exc.detail},
        )
    logger.error(
        f"Unhandled exception on {request.method} {request.url.path}: {exc}",
        exc_info=True,
        extra={"path": str(request.url), "method": request.method},
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred. Please try again later.",
        },
    )


# ──────────────────────────────────────────────
#  REST API Routes
# ──────────────────────────────────────────────
app.router.routes.append(Route("/mcp", StaffMCP(), methods=["GET", "POST", "DELETE"]))

app.include_router(auth_router, prefix="/api", tags=["auth"])
app.include_router(api_router, prefix="/api/v1", tags=["api"])


from app.dependencies import verify_agent_ws_token, verify_customer_ws_token  #review: duplicate import — verify_agent_ws_token already imported on line 26

# ──────────────────────────────────────────────
#  WebSocket Endpoints
# ──────────────────────────────────────────────
@app.websocket("/ws/chat")
async def ws_chat_no_session(websocket: WebSocket, authenticated_customer_id: int = Depends(verify_customer_ws_token)):
    """Customer chat — auto-generates a session ID."""
    await handle_customer_ws(websocket, session_id=None, authenticated_customer_id=authenticated_customer_id)


@app.websocket("/ws/chat/{session_id}")
async def ws_chat(websocket: WebSocket, session_id: str, authenticated_customer_id: int = Depends(verify_customer_ws_token)):
    """Customer chat — resumes an existing session."""
    await handle_customer_ws(websocket, session_id=session_id, authenticated_customer_id=authenticated_customer_id)


@app.websocket("/ws/agent/{session_id}")
async def ws_agent(websocket: WebSocket, session_id: str, _: bool = Depends(verify_agent_ws_token)):
    """Agent dashboard — monitor and take over a customer session."""
    await handle_agent_ws(websocket, session_id=session_id)


# ──────────────────────────────────────────────
#  Health Check
# ──────────────────────────────────────────────
@app.get("/health", tags=["system"])
def health_check():
    from app.llm_factory import last_model_success, resolve_model
    from app.outbox.lifecycle import outbox_health
    from app.tools import check_supabase_health

    model = resolve_model()
    db_health = check_supabase_health()
    status = "healthy" if db_health.get("status") == "healthy" else "unhealthy"
    
    response = {
        "status": status,
        "service": "customer-support-ai",
        "version": app.version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "llm_provider": settings.llm_provider,
        "llm_model": model,
        "last_model_success_at": last_model_success(model),
        "outbox": outbox_health(),
        "supabase_connected": status == "healthy",
        "details": db_health
    }
    
    if status != "healthy":
        return JSONResponse(status_code=503, content=response)
    return response


# ──────────────────────────────────────────────
#  Run with Uvicorn
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
        log_level=settings.log_level.lower(),
    )
