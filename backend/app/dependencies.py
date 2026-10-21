"""Authentication & authorization dependency functions for FastAPI routes/websockets."""

from fastapi import Depends, HTTPException, status, Header, Query, WebSocketException
from typing import Optional
import jwt
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError
from app.config import settings

def verify_admin_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> bool:
    """Verify the admin API key from headers, or fall back to a valid agent/admin JWT."""
    # 1. Check static admin API key
    if settings.admin_api_key and x_api_key == settings.admin_api_key:
        return True

    # 2. Fall back to agent/admin JWT (allows the frontend dashboard to authenticate)
    if authorization and authorization.lower().startswith("bearer "):
        candidate = authorization.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(
                candidate,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            if payload.get("role") in ("agent", "admin"):
                return True
        except (InvalidTokenError, ExpiredSignatureError):
            pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid admin API key or agent authentication is required",
    )

def verify_agent_token(
    x_agent_token: Optional[str] = Header(None, alias="X-Agent-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> bool:
    """Verify a static demo token or a signed agent/admin JWT for REST routes."""
    if settings.agent_secret and x_agent_token == settings.agent_secret:
        return True

    if authorization and authorization.lower().startswith("bearer "):
        candidate = authorization.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(
                candidate,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            if payload.get("role") in ("agent", "admin"):
                return True
        except (InvalidTokenError, ExpiredSignatureError):
            pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid agent authentication is required",
        headers={"WWW-Authenticate": "Bearer"},
    )

async def verify_agent_ws_token(
    agent_secret: Optional[str] = Query(None),
    token: Optional[str] = Query(None),
) -> bool:
    """Verify agent secret or agent JWT token for WebSocket connections."""
    candidate = agent_secret or token

    # Match static agent secret
    if settings.agent_secret and candidate == settings.agent_secret:
        return True

    # Or verify if it's a valid JWT token
    if candidate:
        try:
            payload = jwt.decode(candidate, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            if payload.get("role") in ("agent", "admin"):
                return True
        except Exception:
            pass

    # Static secret not configured at all
    if not settings.agent_secret:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Agent secret not configured")  #review: this means no WS auth at all if agent_secret is unset

    raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid Agent Secret")

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials  #review: import at mid-file — consider moving to top

security = HTTPBearer()  # extracts Bearer token from Authorization header

def verify_customer_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> int:
    """Verify customer JWT token from Authorization header and return customer_id."""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("role") != "customer":
            raise HTTPException(status_code=403, detail="Invalid role")
        
        customer_id_str = payload.get("sub")
        if not customer_id_str:
            raise HTTPException(status_code=401, detail="Invalid token subject")
            
        return int(customer_id_str)
        
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid customer identity format")

def verify_any_token(
    x_agent_token: Optional[str] = Header(None, alias="X-Agent-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> dict:
    """Verify customer, agent, or admin JWT token, or static agent token."""
    # Check static agent secret first
    if settings.agent_secret and x_agent_token == settings.agent_secret:
        return {"role": "agent"}

    if authorization and authorization.lower().startswith("bearer "):
        candidate = authorization.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(candidate, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            role = payload.get("role")
            if role in ("agent", "admin"):
                return {"role": role}
            elif role == "customer":
                sub = payload.get("sub")
                if not sub:
                    raise HTTPException(status_code=401, detail="Invalid token subject")
                try:
                    return {"role": role, "id": int(sub)}
                except ValueError:
                    raise HTTPException(status_code=401, detail="Invalid customer identity format")
        except ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token has expired")
        except InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")

    raise HTTPException(status_code=401, detail="Valid authentication is required")

async def verify_customer_ws_token(token: str = Query(None)) -> int:
    """Verify customer JWT token from WebSocket query parameters."""
    if not token:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")
        
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("role") != "customer":
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid role")
            
        customer_id_str = payload.get("sub")
        if not customer_id_str:
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token subject")
            
        return int(customer_id_str)
        
    except ExpiredSignatureError:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Token has expired")
    except InvalidTokenError:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
    except ValueError:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid customer identity format")
