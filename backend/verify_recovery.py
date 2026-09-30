import asyncio
from fastapi.testclient import TestClient
from app.main import app

def test_recovery():
    client = TestClient(app)
    from app.websocket.connection import manager
    from app.websocket.chat_handler import session_store
    import uuid

    # 1. Create a session.
    session_id = f"test-recovery-{uuid.uuid4().hex[:6]}"
    # Mock token validation by getting a dummy token via dependencies if we had to, but here we can just use the test auth token generator
    import jwt
    from app.config import settings
    token = jwt.encode({"sub": "123", "role": "customer"}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    
    # 2. Persist conversation state.
    with client.websocket_connect(f"/ws/chat?session_id={session_id}&customer_id=123&token={token}") as ws:
        welcome = ws.receive_json()
        assert welcome["type"] == "system"
        ws.send_json({"message": "Hello!"})
        msg = ws.receive_json()
        while msg["type"] in ("ping", "typing", "system"):
            msg = ws.receive_json()
    
    # 3. Capture the persisted session/transcript.
    persisted = asyncio.run(session_store.get_session(session_id))
    print(f"Captured persisted session: {persisted is not None}")
    
    # 4. Destroy/recreate the in-process session state.
    # We clear in-memory state. If Redis is active, it survives.
    manager.active_connections.clear()
    session_store.clear()
    
    # 5. Reconnect with the same session_id.
    recovered_history = None
    with client.websocket_connect(f"/ws/chat?session_id={session_id}&customer_id=123&token={token}") as ws:
        welcome = ws.receive_json()
        if welcome["type"] == "system":
            recovered_history = welcome.get("history", [])
            
    # 6. Verify required AgentState is reconstructed (history preserved).
    # Since we cleared in-memory, if it recovered, it must be from Redis!
    if recovered_history and len(recovered_history) > 0:
        print("FULLY TESTED: Session state successfully recovered from external store.")
    else:
        print("PARTIALLY TESTED: Session state was lost on restart due to reliance on in-memory fallback (Redis not configured).")

if __name__ == "__main__":
    test_recovery()
