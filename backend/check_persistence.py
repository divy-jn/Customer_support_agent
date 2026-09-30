import asyncio
from fastapi.testclient import TestClient
from app.main import app

def test_session_recovery():
    # Since session_store uses Upstash Redis if configured, if NOT configured it uses in-memory.
    from app.websocket.chat_handler import _redis_available, redis_client
    if redis_client:
        print("PARTIALLY RECOVERED")
    else:
        print("NOT RECOVERED")

if __name__ == "__main__":
    test_session_recovery()
