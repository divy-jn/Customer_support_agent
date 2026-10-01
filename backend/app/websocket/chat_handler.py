"""
WebSocket Chat Handler — processes incoming customer messages
through the LangGraph pipeline and streams responses back.
"""

import uuid
import json
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect

from app.config import settings
from app.websocket.connection import manager
from app.agents.graph import customer_support_graph, AgentState
from app.email_service import send_escalation_email
from app.tools import lookup_customer
from app.guardrails import validate_input, validate_output, get_rejection_message

from app.persistence.session import session_store

logger = logging.getLogger(__name__)


async def _execute_cas(session_id: str, loader_fn, mutator_fn, retries: int) -> tuple[dict, bool]:
    from app.persistence.session import SaveResult
    for attempt in range(retries):
        session = await loader_fn()
        expected_revision = session.get("_session_revision", 1)
        
        should_save = mutator_fn(session)
        if not should_save:
            return session, False
            
        result = await session_store.save_session_conditional(session_id, session, expected_revision)
        
        if result == SaveResult.SUCCESS:
            return session, True
        elif result == SaveResult.MISSING:
            logger.error("Session missing during CAS mutation", extra={"session_id": session_id})
            raise RuntimeError(f"Session {session_id} missing")
            
        logger.warning(
            f"CAS conflict on session {session_id}, attempt {attempt+1}/{retries}", 
            extra={
                "session_id": session_id,
                "expected_revision": expected_revision,
                "retry_count": attempt + 1
            }
        )
                      
    logger.error("CAS conflict exhausted retries or retry disabled", extra={"session_id": session_id, "retry_count": retries})
    raise RuntimeError("Concurrency conflict: stale session data could not be saved safely.")


async def _get_session(session_id: str, authenticated_customer_id: int | None = None) -> dict:
    """Retrieve an existing session or initialize a new one with the authenticated customer_id."""
    session = await session_store.get_session(session_id)
    if not session:
        # Initialize new session bounded to the authenticated customer
        new_session = {
            "session_id": session_id,
            "customer_id": authenticated_customer_id,
            "_session_revision": 1,
            "mode": "ai",
            "conversation_history": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pending_approval": None,
            "workflow_state": {
                "active_domain": None,
                "state_revision": 1,
                "session_id": session_id,
                "customer_id": authenticated_customer_id,
                "turn_count": 0
            }
        }
        from app.persistence.session import SaveResult
        res = await session_store.create_session_if_absent(session_id, new_session)
        if res == SaveResult.SUCCESS:
            return new_session
        
        # If it failed to create, it was created concurrently. Fall through to load it.
        session = await session_store.get_session(session_id)
        if not session:
            raise RuntimeError("Session creation race but session is missing")

    # Enforce session ownership
    if authenticated_customer_id is not None:
        if session.get("customer_id") is not None and session.get("customer_id") != authenticated_customer_id:
            raise ValueError(f"Session {session_id} belongs to a different customer.")
        if session.get("customer_id") is None:
            async def _load_raw():
                s = await session_store.get_session(session_id)
                if not s:
                    raise RuntimeError(f"Session {session_id} missing")
                # OWNERSHIP REVALIDATION AFTER RELOAD
                if s.get("customer_id") is not None and s.get("customer_id") != authenticated_customer_id:
                    raise ValueError(f"Session {session_id} belongs to a different customer.")
                return s
                
            def _init_customer(s):
                if s.get("customer_id") is not None:
                    return False
                s["customer_id"] = authenticated_customer_id
                return True
                
            session, _ = await _execute_cas(session_id, _load_raw, _init_customer, 3)
            
    return session



async def _mutate_session(session_id: str, mutator_fn, allow_retry: bool = True, customer_id: int | None = None) -> tuple[dict, bool]:
    MAX_CAS_RETRIES = 3
    retries = MAX_CAS_RETRIES if allow_retry else 1
    
    async def _load_fn():
        return await _get_session(session_id, customer_id)
        
    return await _execute_cas(session_id, _load_fn, mutator_fn, retries)


async def get_all_active_sessions() -> list:
    """Retrieve all active sessions from Redis or memory."""
    return await session_store.get_all_active_sessions()


async def handle_customer_ws(websocket: WebSocket, session_id: str | None = None, authenticated_customer_id: int | None = None):
    """
    Main handler for customer WebSocket connections.
    Receives messages, processes them through LangGraph, and sends responses.
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    await manager.connect_customer(websocket, session_id)

    try:
        session = await _get_session(session_id, authenticated_customer_id)
    except ValueError as e:
        # Reject connection if ownership fails
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(e))
        return

    customer_id = authenticated_customer_id
    history = session.get("conversation_history", [])

    # Send welcome message with session ID and history
    await manager.send_personal_message({
        "type": "system",
        "session_id": session_id,
        "mode": session.get("mode", "ai"),
        "message": "Hello! Welcome to our customer support. How can I help you today?",
        "agent_name": "Adi",
        "history": history,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, session_id)

    import time
    msg_timestamps = []
    rate_limit_window = 60
    rate_limit_max = settings.ws_message_rate_limit

    try:
        while True:
            # Receive message from customer
            try:
                raw = await websocket.receive_text()
            except RuntimeError:
                logger.warning(f"WebSocket {session_id} receive_text failed (likely disconnected already).")
                break

            now = time.time()
            msg_timestamps = [t for t in msg_timestamps if now - t < rate_limit_window]
            if len(msg_timestamps) >= rate_limit_max:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Rate limit exceeded. Please wait a moment before sending more messages."
                }))
                continue
            msg_timestamps.append(now)

            try:
                data = json.loads(raw)
                message = data.get("message", "").strip()
                client_request_id = data.get("client_request_id")
                # Ignore customer_id in JSON payload, use authenticated_customer_id
            except json.JSONDecodeError:
                message = raw.strip()
                client_request_id = None
                
            if not client_request_id:
                import uuid
                client_request_id = str(uuid.uuid4())

            customer_id = authenticated_customer_id

            if not message:
                continue

            if message.startswith("[System]"):
                # Ignore system messages sent incorrectly from older frontends
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

            def _append_customer(s):
                s["conversation_history"].append({
                    "role": "customer",
                    "content": message,
                    "sanitized": sanitized_message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "pii_detected": guard_result.pii_detected,
                })
                return True
            session, _ = await _mutate_session(session_id, _append_customer, allow_retry=True, customer_id=customer_id)

            # Broadcast to monitoring agents
            await manager.broadcast_to_agents({
                "type": "customer_message",
                "session_id": session_id,
                "customer_id": customer_id,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, session_id)

            # If human agent has taken over, do NOT run ANY AI logic
            if session.get("mode") == "human":
                continue

            # Check if this is an approval response for a pending action
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
                    
                    def _approve_execute(s):
                        s["conversation_history"].append({
                            "role": "agent", "content": response_text,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                        s.pop("pending_approval", None)
                        return True
                    session, _ = await _mutate_session(session_id, _approve_execute, allow_retry=True)
                    
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
                def _approve_reject(s):
                    s.pop("pending_approval", None)
                    s["conversation_history"].append({
                        "role": "agent",
                        "content": "No problem! I've cancelled that action. Is there anything else I can help with?",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    return True
                session, _ = await _mutate_session(session_id, _approve_reject, allow_retry=True)
                await manager.send_personal_message({
                    "type": "agent_response",
                    "message": "No problem! I've cancelled that action. Is there anything else I can help with?",
                    "agent_name": "Adi",
                    "escalated": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)
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
                    "customer_name": session.get("customer_name"),
                    "session_id": session_id,
                    "client_request_id": client_request_id,
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
                    "workflow_state": session.get("workflow_state", {
                        "session_id": session_id,
                        "customer_id": customer_id,
                        "turn_count": 0
                    }),
                }

                # Invoke LangGraph with timeout
                timeout_secs = settings.langgraph_timeout
                try:
                    result = await asyncio.wait_for(
                        customer_support_graph.ainvoke(initial_state),
                        timeout=timeout_secs,
                    )
                except asyncio.TimeoutError:
                    logger.error(f"LangGraph timed out after {timeout_secs}s for session {session_id}")
                    await manager.send_personal_message({
                        "type": "agent_response",
                        "message": "I'm taking longer than expected to process your request. Please try again or let me connect you with a human agent.",
                        "agent_name": "Adi",
                        "escalated": False,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }, session_id)
                    continue

                # We still re-check mode but the whole mutation must be atomic
                raw_response = result.get("response", "I'm sorry, I couldn't process your request.")
                is_escalated = result.get("escalated", False)

                # ── Output Guardrails ──
                output_guard = validate_output(raw_response)
                response_text = output_guard.sanitized_text
                if output_guard.violations:
                    logger.warning(
                        f"Output guardrails triggered for session {session_id}",
                        extra={"violations": output_guard.violations, "risk_score": output_guard.risk_score},
                    )

                def _apply_graph_result(s):
                    if s.get("mode") == "human":
                        return False # do not save, human took over
                    
                    if result.get("pending_approval"):
                        s["pending_approval"] = result["pending_approval"]
                        s["pending_approval"]["original_message"] = message
                        s["pending_approval"]["intent"] = result.get("intent", "")

                    if result.get("workflow_state"):
                        s["workflow_state"] = result["workflow_state"]

                    s["conversation_history"].append({
                        "role": "agent",
                        "content": response_text,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    return True

                try:
                    # NEVER blindly merge arbitrary typed domain state. 
                    session, was_changed = await _mutate_session(session_id, _apply_graph_result, allow_retry=False, customer_id=customer_id)
                    if not was_changed:
                        logger.info(f"AI response discarded for {session_id} - agent took over during processing.")
                        continue
                except RuntimeError as e:
                    if "Concurrency conflict" in str(e):
                        logger.error(f"CAS conflict on graph result for session {session_id}: {e}")
                        await manager.send_personal_message({
                            "type": "error",
                            "message": "The conversation was updated in another window. Please try your request again.",
                            "agent_name": "Adi",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }, session_id)
                        continue
                    raise e

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
                    # Send escalation alert to ALL dashboard agents so they can pick it up
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
                        
                        await asyncio.to_thread(
                            send_escalation_email,
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
        pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Error in customer websocket: {e}")
        
    finally:
        manager.disconnect_customer(session_id, websocket)
        logger.info(f"Customer disconnected: {session_id}")
        
        # Save conversation to database.
        # We use local variables to avoid `await` which would raise CancelledError if the task is cancelled.
        try:
            # Only save if we have initialized the session earlier in the function
            if 'session' in locals() and session and session.get("conversation_history"):
                was_escalated = "true" if session.get("mode") == "human" or any(
                    msg.get("escalated") for msg in session.get("conversation_history", [])
                ) else "false"
                
                data = {
                    "session_id": session_id,
                    "customer_id": session.get("customer_id") or 1,
                    "transcript": session.get("conversation_history"),
                    "escalated": True if was_escalated == "true" else False,
                    "ended_at": datetime.now(timezone.utc).isoformat()
                }
                from app.tools import supabase
                import anyio
                
                # Instead of asyncio.shield (which fails under anyio cancellation),
                # we spawn a background thread natively so it survives cancellation
                # and we don't await it here to prevent CancelledError.
                # This satisfies the requirement of not blocking the event loop
                # while ensuring the write happens.
                def save_to_db(save_data):
                    try:
                        supabase.table("conversations").upsert(save_data, on_conflict="session_id").execute()
                    except Exception as ex:
                        pass
                
                import threading
                t = threading.Thread(target=save_to_db, args=(data,))
                t.start()
                # In tests we might need it to finish, so we join with a short timeout.
                # This safely waits for the persistence operation without raising CancelledError.
                t.join(timeout=2.0)
                
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
            try:
                raw = await websocket.receive_text()
            except RuntimeError as e:
                logger.warning(f"WebSocket RuntimeError (agent likely disconnected): {e}")
                break
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
                def _agent_message(s):
                    s["conversation_history"].append({
                        "role": "agent",
                        "content": agent_message,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "human_agent": True,
                        "agent_name": agent_name,
                    })
                    return True
                session, _ = await _mutate_session(session_id, _agent_message, allow_retry=True)

                # Send to customer
                await manager.send_personal_message({
                    "type": "human_message",
                    "session_id": session_id,
                    "message": agent_message,
                    "agent_name": agent_name,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)

            elif msg_type == "takeover":
                # Agent is taking over the conversation
                def _takeover(s):
                    if s.get("mode") != "human":
                        s["mode"] = "human"
                        return True
                    return False
                session, was_changed = await _mutate_session(session_id, _takeover, allow_retry=True)
                if was_changed and session and session.get("mode") == "human":
                    await manager.send_personal_message({
                        "type": "mode_change",
                        "mode": "human",
                        "agent_name": data.get("agent_name", "Support Agent"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }, session_id)

            elif msg_type == "release":
                # Agent is returning the conversation to the AI
                def _release(s):
                    if s.get("mode") != "ai":
                        s["mode"] = "ai"
                        return True
                    return False
                session, was_changed = await _mutate_session(session_id, _release, allow_retry=True)
                if was_changed and session and session.get("mode") == "ai":
                    await manager.send_personal_message({
                        "type": "mode_change",
                        "mode": "ai",
                        "agent_name": data.get("agent_name", "AI Assistant"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }, session_id)

    except WebSocketDisconnect:
        manager.disconnect_agent(websocket, session_id)
        logger.info(f"Agent disconnected from session: {session_id}")
        
        # Safely return session to AI mode if it was abandoned in human mode
        # Check if there are still any other agents connected to this session
        has_other_agents = bool(manager.agent_connections.get(session_id))
        
        def _abandon(s):
            if s and s.get("mode") == "human" and not has_other_agents:
                s["mode"] = "ai"
                return True
            return False
            
        try:
            session, was_changed = await _mutate_session(session_id, _abandon, allow_retry=True)
            if was_changed and session and session.get("mode") == "ai" and not has_other_agents:
                # Notify customer that AI has resumed
                await manager.send_personal_message({
                    "type": "mode_change",
                    "mode": "ai",
                    "agent_name": "AI Assistant",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)
        except Exception as e:
            logger.error(f"Failed to abandon human mode for session {session_id}: {e}")
