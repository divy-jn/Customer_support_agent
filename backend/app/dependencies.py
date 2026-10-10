from fastapi import Depends, HTTPException, status, Header, Query, WebSocketException
from typing import Optional
import jwt
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError
from app.config import settings

def verify_admin_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")) -> bool:
    """Verify the admin API key from headers."""
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin API key is not configured on the server"
        )
    
    if x_api_key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Admin API Key"
        )
    return True

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

    if not settings.agent_secret:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Agent secret not configured")

    raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid Agent Secret")

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

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
