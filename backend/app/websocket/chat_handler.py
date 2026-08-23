"""
WebSocket Chat Handler — processes incoming customer messages
through the LangGraph pipeline and streams responses back.
"""

import uuid
import json
import logging
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect

from app.config import settings
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
        session_store[session_id] = session

async def get_all_active_sessions() -> list:
    """Retrieve all active sessions from Redis or memory."""
    if redis_client:
        try:
            keys = await redis_client.keys("session:*")
            sessions = []
            if keys:
                values = await redis_client.mget(*keys)
                for val in values:
                    if val:
                        session = json.loads(val) if isinstance(val, str) else val
                        sessions.append(session)
            return sessions
        except Exception as e:
            logger.error(f"Failed to fetch sessions from Redis: {e}")
            return []
    else:
        return list(session_store.values())


async def handle_customer_ws(websocket: WebSocket, session_id: str | None = None):
    """
    Main handler for customer WebSocket connections.
    Receives messages, processes them through LangGraph, and sends responses.
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    await manager.connect_customer(websocket, session_id)

    # Send welcome message with session ID
    await manager.send_personal_message({
        "type": "system",
        "session_id": session_id,
        "message": "Hello! Welcome to our customer support. How can I help you today?",
        "agent_name": "Adi",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, session_id)

    try:
        while True:
            # Receive message from customer
            raw = await websocket.receive_text()

            try:
                data = json.loads(raw)
                message = data.get("message", "").strip()
                customer_id = data.get("customer_id")
            except json.JSONDecodeError:
                message = raw.strip()
                customer_id = None

            if not message:
                continue

            # ── Input Guardrails ──
            guard_result = validate_input(message)
            if not guard_result.passed:
                rejection = get_rejection_message(guard_result)
                logger.warning(
                    f"Input blocked for session {session_id}",
                    extra={"violations": guard_result.violations, "risk_score": guard_result.risk_score},
                )
                await manager.send_personal_message({
                    "type": "agent_response",
                    "message": rejection,
                    "agent_name": "Adi",
                    "escalated": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)
                continue

            # Use sanitized message (PII redacted for logging)
            sanitized_message = guard_result.sanitized_text

            # Check if this is an approval response for a pending action
            session = await _get_session(session_id, customer_id)
            pending = session.get("pending_approval")
            
            if pending and message.lower().strip() in ("yes", "yeah", "yep", "sure", "ok", "okay", "go ahead", "proceed", "confirm", "do it"):
                # Customer approved the pending action — execute it
                await manager.send_personal_message({
                    "type": "typing", "agent_name": "Adi",
                }, session_id)
                
                try:
                    execute_state: AgentState = {
                        "customer_id": customer_id,
                        "session_id": session_id,
                        "message": pending.get("original_message", message),
                        "conversation_history": session["conversation_history"],
                        "intent": pending.get("intent", ""),
                        "sentiment": "",
                        "urgency": "",
                        "route_to": "db_agent",
                        "tool_results": None,
                        "response": None,
                        "escalated": False,
                        "pending_approval": pending,
                        "approval_granted": True,
                    }
                    
                    from app.agents.graph import customer_support_graph as exec_graph
                    # Directly invoke the execute node
                    from app.agents.graph import db_execute_node
                    result = await db_execute_node(execute_state)
                    
                    raw_response = result.get("response", "Done!")
                    output_guard = validate_output(raw_response)
                    response_text = output_guard.sanitized_text
                    
                    session["conversation_history"].append({
                        "role": "agent", "content": response_text,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    session.pop("pending_approval", None)
                    await _save_session(session_id, session)
                    
                    await manager.send_personal_message({
                        "type": "agent_response",
                        "message": response_text,
                        "agent_name": "Adi",
                        "escalated": False,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }, session_id)
                    
                except Exception as e:
                    logger.error(f"Approval execution error: {e}")
                    await manager.send_personal_message({
                        "type": "agent_response",
                        "message": "I'm sorry, something went wrong while processing that. Let me connect you with a human agent.",
                        "agent_name": "Adi",
                        "escalated": False,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }, session_id)
                continue
            
            elif pending and message.lower().strip() in ("no", "nope", "nah", "cancel", "don't", "dont", "stop", "never mind"):
                # Customer rejected the pending action
                session.pop("pending_approval", None)
                session["conversation_history"].append({
                    "role": "agent",
                    "content": "No problem! I've cancelled that action. Is there anything else I can help with?",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                await _save_session(session_id, session)
                await manager.send_personal_message({
                    "type": "agent_response",
                    "message": "No problem! I've cancelled that action. Is there anything else I can help with?",
                    "agent_name": "Adi",
                    "escalated": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)
                continue

            # Append customer message to history (sanitized)
            session["conversation_history"].append({
                "role": "customer",
                "content": message,  # Keep original for LLM processing
                "sanitized": sanitized_message,  # Sanitized for logging
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "pii_detected": guard_result.pii_detected,
            })
            await _save_session(session_id, session)

            # Broadcast to monitoring agents
            await manager.broadcast_to_agents({
                "type": "customer_message",
                "session_id": session_id,
                "customer_id": customer_id,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, session_id)

            # If human agent has taken over, do NOT run the AI
            if session.get("human_takeover", False):
                continue

            # Send typing indicator (only when AI will actually process)
            await manager.send_personal_message({
                "type": "typing",
                "agent_name": "Adi",
            }, session_id)

            # Run LangGraph
            try:
                initial_state: AgentState = {
                    "customer_id": customer_id,
                    "session_id": session_id,
                    "message": message,
                    "conversation_history": session["conversation_history"],
                    "intent": "",
                    "sentiment": "",
                    "urgency": "",
                    "route_to": "",
                    "tool_results": None,
                    "response": None,
                    "escalated": False,
                    "pending_approval": None,
                    "approval_granted": None,
                }

                result = await customer_support_graph.ainvoke(initial_state)

                raw_response = result.get("response", "I'm sorry, I couldn't process your request.")
                is_escalated = result.get("escalated", False)
                
                # Check if graph returned a pending approval
                if result.get("pending_approval"):
                    session["pending_approval"] = result["pending_approval"]
                    session["pending_approval"]["original_message"] = message
                    session["pending_approval"]["intent"] = result.get("intent", "")

                # ── Output Guardrails ──
                output_guard = validate_output(raw_response)
                response_text = output_guard.sanitized_text
                if output_guard.violations:
                    logger.warning(
                        f"Output guardrails triggered for session {session_id}",
                        extra={"violations": output_guard.violations, "risk_score": output_guard.risk_score},
                    )

                # Append agent response to history
                session["conversation_history"].append({
                    "role": "agent",
                    "content": response_text,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                await _save_session(session_id, session)

                # Send response to customer
                response_payload = {
                    "type": "agent_response",
                    "session_id": session_id,
                    "message": response_text,
                    "intent": result.get("intent", ""),
                    "sentiment": result.get("sentiment", ""),
                    "urgency": result.get("urgency", ""),
                    "escalated": is_escalated,
                    "agent_name": "Adi",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                await manager.send_personal_message(response_payload, session_id)

                # Broadcast to monitoring agents
                await manager.broadcast_to_agents({
                    "type": "agent_response",
                    **response_payload,
                }, session_id)

                # If escalated, notify all connected dashboard agents
                if is_escalated:
                    escalation_msg = {
                        "type": "escalation_alert",
                        "session_id": session_id,
                        "customer_id": customer_id,
                        "sentiment": result.get("sentiment", "negative"),
                        "urgency": result.get("urgency", "high"),
                        "last_message": message,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    await manager.broadcast_to_agents(escalation_msg, session_id)
                    await manager.broadcast_to_all_agents(escalation_msg)

                    # Send escalation email
                    try:
                        customer_email = None
                        customer_name = "Unknown Customer"
                        if customer_id:
                            cust_res = lookup_customer(str(customer_id))
                            cust_data = json.loads(cust_res)
                            if isinstance(cust_data, list) and cust_data:
                                customer_email = cust_data[0].get("email")
                                customer_name = cust_data[0].get("name", customer_name)
                        
                        send_escalation_email(
                            customer_email=customer_email,
                            customer_name=customer_name,
                            session_id=session_id,
                            sentiment=result.get("sentiment", "negative"),
                            urgency=result.get("urgency", "high"),
                            last_message=message,
                        )
                    except Exception as e:
                        logger.error(f"Failed to send escalation email for session {session_id}: {e}")

            except Exception as e:
                logger.error(f"LangGraph error for session {session_id}: {e}")
                await manager.send_personal_message({
                    "type": "error",
                    "message": "I'm experiencing a technical issue. Let me connect you with a human agent.",
                    "agent_name": "Adi",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)

    except WebSocketDisconnect:
        manager.disconnect_customer(session_id)
        logger.info(f"Customer disconnected: {session_id}")
        
        # Save conversation to database
        session = await _get_session(session_id)
        if session and session.get("conversation_history"):
            try:
                from app.database import AsyncSessionLocal, Conversation, _utcnow
                async with AsyncSessionLocal() as db:
                    # Determine if it was escalated
                    was_escalated = "true" if session.get("human_takeover") or any(
                        msg.get("escalated") for msg in session.get("conversation_history", [])
                    ) else "false"
                    
                    conv = Conversation(
                        session_id=session_id,
                        customer_id=session.get("customer_id") or 1,  # Default to guest (1) if missing
                        transcript=json.dumps(session.get("conversation_history")),
                        escalated=was_escalated,
                        ended_at=_utcnow()
                    )
                    db.add(conv)
                    await db.commit()
            except Exception as e:
                logger.error(f"Failed to save conversation to DB for session {session_id}: {e}")

async def handle_agent_ws(websocket: WebSocket, session_id: str):
    """
    Handler for agent dashboard WebSocket connections.
    Allows agents to monitor and take over customer conversations.
    """
    await manager.connect_agent(websocket, session_id)

    # Send current conversation history to the agent
    session = await _get_session(session_id)
    await websocket.send_text(json.dumps({
        "type": "session_state",
        "session_id": session_id,
        "conversation_history": session.get("conversation_history", []),
        "customer_id": session.get("customer_id"),
    }))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            if msg_type == "agent_message":
                # Human agent is sending a message to the customer
                agent_message = data.get("message", "").strip()
                agent_name = data.get("agent_name", "Support Agent")

                if not agent_message:
                    continue

                # Append to history
                session = await _get_session(session_id)
                session["conversation_history"].append({
                    "role": "agent",
                    "content": agent_message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "human_agent": True,
                    "agent_name": agent_name,
                })
                await _save_session(session_id, session)

                # Send to customer
                await manager.send_personal_message({
                    "type": "agent_response",
                    "session_id": session_id,
                    "message": agent_message,
                    "agent_name": agent_name,
                    "escalated": True,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)

            elif msg_type == "takeover":
                # Agent is taking over the conversation
                session = await _get_session(session_id)
                session["human_takeover"] = True
                await _save_session(session_id, session)
                
                await manager.send_personal_message({
                    "type": "system",
                    "message": f"You are now connected with {data.get('agent_name', 'a human agent')}.",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)

    except WebSocketDisconnect:
        manager.disconnect_agent(websocket, session_id)
        logger.info(f"Agent disconnected from session: {session_id}")
