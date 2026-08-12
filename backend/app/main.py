"""
FastAPI Application Entry Point — main.py

Serves:
- REST API on /api/v1/*
- WebSocket for customer chat on /ws/chat/{session_id}
- WebSocket for agent dashboard on /ws/agent/{session_id}
- Health check on /health
"""

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.routes import router as api_router
from app.websocket.chat_handler import handle_customer_ws, handle_agent_ws

from app.logger import setup_logger, set_trace_id

# ──────────────────────────────────────────────
#  Logging
# ──────────────────────────────────────────────
setup_logger(log_level=settings.log_level)
logger = logging.getLogger("customer_support")


# ──────────────────────────────────────────────
#  Request ID Middleware
# ──────────────────────────────────────────────
class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique X-Request-ID to every request for tracing."""

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
    import urllib.request
    import json as _json

    checks = {}

    # LLM
    try:
        req = urllib.request.Request(
            f"{settings.llm_base_url.rstrip('/')}/models",
            headers={"User-Agent": "IntelliSupport/1.0", "Authorization": f"Bearer {settings.llm_api_key}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
            models = [m.get("id", "") for m in data.get("data", [])]
            checks["llm"] = f"OK ({len(models)} models)"
    except Exception as e:
        checks["llm"] = f"UNREACHABLE ({e})"
        logger.warning(f"LLM API not reachable at startup: {e}")

    # Supabase
    try:
        from supabase import create_client
        sb = create_client(settings.supabase_url, settings.supabase_key)
        sb.table("customers").select("id").limit(1).execute()
        checks["supabase"] = "OK"
    except Exception as e:
        checks["supabase"] = f"UNREACHABLE ({e})"
        logger.warning(f"Supabase not reachable at startup: {e}")

    # Vector DB (Pinecone)
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
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== Customer Support AI Server Starting ===")
    logger.info(f"LLM URL       : {settings.llm_base_url}")
    logger.info(f"Small Model   : {settings.llm_small_model}")
    logger.info(f"Large Model   : {settings.llm_large_model}")
    logger.info(f"Supabase URL  : {settings.supabase_url}")
    logger.info(f"Log Level     : {settings.log_level}")
    logger.info(f"CORS Origins  : {settings.cors_origins_list}")

    # Run startup health checks (non-blocking)
    try:
        _run_startup_checks()
    except Exception as e:
        logger.error(f"Startup checks failed: {e}")

    yield
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

# CORS — use explicit origins (allow_origins=["*"] + allow_credentials=True is invalid per spec)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

# Rate Limiting — 300 requests/minute per IP
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded

    limiter = Limiter(key_func=get_remote_address, default_limits=["300/minute"])
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    logger.info("Rate limiting enabled: 300 req/min per IP")
except ImportError:
    logger.warning("slowapi not installed — rate limiting disabled. Run: pip install slowapi")


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
app.include_router(api_router, prefix="/api/v1", tags=["api"])


# ──────────────────────────────────────────────
#  WebSocket Endpoints
# ──────────────────────────────────────────────
@app.websocket("/ws/chat")
async def ws_chat_no_session(websocket: WebSocket):
    """Customer chat — auto-generates a session ID."""
    await handle_customer_ws(websocket, session_id=None)


@app.websocket("/ws/chat/{session_id}")
async def ws_chat(websocket: WebSocket, session_id: str):
    """Customer chat — resumes an existing session."""
    await handle_customer_ws(websocket, session_id=session_id)


@app.websocket("/ws/agent/{session_id}")
async def ws_agent(websocket: WebSocket, session_id: str):
    """Agent dashboard — monitor and take over a customer session."""
    await handle_agent_ws(websocket, session_id=session_id)


# ──────────────────────────────────────────────
#  Health Check
# ──────────────────────────────────────────────
@app.get("/health", tags=["system"])
async def health_check():
    from app.tools import check_supabase_health
    db_health = check_supabase_health()
    status = "healthy" if db_health.get("status") == "healthy" else "unhealthy"
    
    response = {
        "status": status,
        "service": "customer-support-ai",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "llm_url": settings.llm_base_url,
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
