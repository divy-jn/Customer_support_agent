import sys

def main():
    with open('app/websocket/chat_handler.py', 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Replace top imports and store
    top_old = '''from app.websocket.connection import manager
from app.agents.graph import customer_support_graph, AgentState
from app.email_service import send_escalation_email
from app.tools import lookup_customer
from app.guardrails import validate_input, validate_output, get_rejection_message

logger = logging.getLogger(__name__)

# In-memory session store (conversation history per session)
# In production, this would be backed by Redis or the DB.
session_store: dict[str, dict] = {}


def _get_session(session_id: str, customer_id: int | None = None) -> dict:
    """Get or create a session."""
    if session_id not in session_store:
        session_store[session_id] = {
            "session_id": session_id,
            "customer_id": customer_id,
            "conversation_history": [],
            "human_takeover": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    return session_store[session_id]'''

    top_new = '''from app.config import settings
from app.websocket.connection import manager
from app.agents.graph import customer_support_graph, AgentState
from app.email_service import send_escalation_email
from app.tools import lookup_customer
from app.guardrails import validate_input, validate_output, get_rejection_message
from upstash_redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Initialize Upstash Redis if configured
redis_client = None
if settings.upstash_redis_url and settings.upstash_redis_token:
    redis_client = Redis(url=settings.upstash_redis_url, token=settings.upstash_redis_token)
else:
    logger.warning("Upstash Redis not configured. Using in-memory store.")

session_store: dict[str, dict] = {}


async def _get_session(session_id: str, customer_id: int | None = None) -> dict:
    """Get or create a session."""
    if redis_client:
        data = await redis_client.get(f"session:{session_id}")
        if data:
            return json.loads(data) if isinstance(data, str) else data
    else:
        if session_id in session_store:
            return session_store[session_id]

    session = {
        "session_id": session_id,
        "customer_id": customer_id,
        "conversation_history": [],
        "human_takeover": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if not redis_client:
        session_store[session_id] = session
    return session


async def _save_session(session_id: str, session: dict):
    """Save session back to Redis (or memory)."""
    if redis_client:
        await redis_client.set(f"session:{session_id}", json.dumps(session), ex=86400)
    else:
        session_store[session_id] = session'''

    content = content.replace(top_old, top_new)
    
    # 1. session = _get_session -> await _get_session
    content = content.replace('session = _get_session(session_id, customer_id)', 'session = await _get_session(session_id, customer_id)')
    
    # 2. Add awaits
    content = content.replace(
        'session.pop("pending_approval", None)',
        'session.pop("pending_approval", None)\n                    await _save_session(session_id, session)',
        1
    )
    
    content = content.replace(
        'session["conversation_history"].append({\n                    "role": "agent",\n                    "content": "No problem! I\\'ve cancelled that action. Is there anything else I can help with?",\n                    "timestamp": datetime.now(timezone.utc).isoformat(),\n                })',
        'session["conversation_history"].append({\n                    "role": "agent",\n                    "content": "No problem! I\\'ve cancelled that action. Is there anything else I can help with?",\n                    "timestamp": datetime.now(timezone.utc).isoformat(),\n                })\n                await _save_session(session_id, session)'
    )
    
    content = content.replace(
        '                "pii_detected": guard_result.pii_detected,\n            })',
        '                "pii_detected": guard_result.pii_detected,\n            })\n            await _save_session(session_id, session)'
    )
    
    content = content.replace(
        '                session["conversation_history"].append({\n                    "role": "agent",\n                    "content": response_text,\n                    "timestamp": datetime.now(timezone.utc).isoformat(),\n                })',
        '                session["conversation_history"].append({\n                    "role": "agent",\n                    "content": response_text,\n                    "timestamp": datetime.now(timezone.utc).isoformat(),\n                })\n                await _save_session(session_id, session)'
    )
    
    content = content.replace(
        '                        session["human_takeover"] = True',
        '                        session["human_takeover"] = True\n                        await _save_session(session_id, session)'
    )
    
    # In handle_agent_ws:
    content = content.replace(
        'session = session_store.get(session_id, {})',
        'session = await _get_session(session_id)'
    )
    
    content = content.replace(
        '                if session_id in session_store:\n                    session_store[session_id]["conversation_history"].append({\n                        "role": "agent",\n                        "content": agent_message,\n                        "timestamp": datetime.now(timezone.utc).isoformat(),\n                        "human_agent": True,\n                        "agent_name": agent_name,\n                    })',
        '                session = await _get_session(session_id)\n                session["conversation_history"].append({\n                    "role": "agent",\n                    "content": agent_message,\n                    "timestamp": datetime.now(timezone.utc).isoformat(),\n                    "human_agent": True,\n                    "agent_name": agent_name,\n                })\n                await _save_session(session_id, session)'
    )
    
    content = content.replace(
        '                if session_id in session_store:\n                    session_store[session_id]["human_takeover"] = True',
        '                session = await _get_session(session_id)\n                session["human_takeover"] = True\n                await _save_session(session_id, session)'
    )
    
    with open('app/websocket/chat_handler.py', 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    main()
