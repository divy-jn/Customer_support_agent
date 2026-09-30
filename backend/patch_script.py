import re

with open("app/websocket/chat_handler.py", "r") as f:
    content = f.read()

# 1. Add _mutate_session
mutate_fn = """
async def _mutate_session(session_id: str, mutator_fn, allow_retry: bool = True, customer_id: int | None = None) -> dict:
    from app.persistence.session import SaveResult
    MAX_CAS_RETRIES = 3
    retries = MAX_CAS_RETRIES if allow_retry else 1
    
    for attempt in range(retries):
        session = await _get_session(session_id, customer_id)
        expected_revision = session.get("_session_revision", 1)
        
        should_save = mutator_fn(session)
        if not should_save:
            return session
            
        result = await session_store.save_session_conditional(session_id, session, expected_revision)
        
        if result == SaveResult.SUCCESS:
            return session
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
"""

content = content.replace("async def _save_session(session_id: str, session: dict):", mutate_fn + "\nasync def _save_session(session_id: str, session: dict):")

# 2. Customer message append
customer_msg_old = """            session = await _get_session(session_id, customer_id)

            # Append customer message to history (sanitized)
            session["conversation_history"].append({
                "role": "customer",
                "content": message,  # Keep original for LLM processing
                "sanitized": sanitized_message,  # Sanitized for logging
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "pii_detected": guard_result.pii_detected,
            })
            await _save_session(session_id, session)"""

customer_msg_new = """            def _append_customer(s):
                s["conversation_history"].append({
                    "role": "customer",
                    "content": message,
                    "sanitized": sanitized_message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "pii_detected": guard_result.pii_detected,
                })
                return True
            session = await _mutate_session(session_id, _append_customer, allow_retry=True, customer_id=customer_id)"""
content = content.replace(customer_msg_old, customer_msg_new)

# 3. Approval execute
approve_exec_old = """                    session["conversation_history"].append({
                        "role": "agent", "content": response_text,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    session.pop("pending_approval", None)
                    await _save_session(session_id, session)"""
approve_exec_new = """                    def _approve_execute(s):
                        s["conversation_history"].append({
                            "role": "agent", "content": response_text,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                        s.pop("pending_approval", None)
                        return True
                    session = await _mutate_session(session_id, _approve_execute, allow_retry=True)"""
content = content.replace(approve_exec_old, approve_exec_new)

# 4. Approval reject
approve_reject_old = """                # Customer rejected the pending action
                session.pop("pending_approval", None)
                session["conversation_history"].append({
                    "role": "agent",
                    "content": "No problem! I've cancelled that action. Is there anything else I can help with?",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                await _save_session(session_id, session)"""
approve_reject_new = """                # Customer rejected the pending action
                def _approve_reject(s):
                    s.pop("pending_approval", None)
                    s["conversation_history"].append({
                        "role": "agent",
                        "content": "No problem! I've cancelled that action. Is there anything else I can help with?",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    return True
                session = await _mutate_session(session_id, _approve_reject, allow_retry=True)"""
content = content.replace(approve_reject_old, approve_reject_new)

# 5. Graph execution
graph_exec_old = """                # RE-CHECK MODE AFTER AWAIT TO PREVENT RACE CONDITIONS
                session = await _get_session(session_id, customer_id)
                if session.get("mode") == "human":
                    logger.info(f"AI response discarded for {session_id} - agent took over during processing.")
                    continue

                raw_response = result.get("response", "I'm sorry, I couldn't process your request.")
                is_escalated = result.get("escalated", False)
                
                # Check if graph returned a pending approval
                if result.get("pending_approval"):
                    session["pending_approval"] = result["pending_approval"]
                    session["pending_approval"]["original_message"] = message
                    session["pending_approval"]["intent"] = result.get("intent", "")

                if result.get("workflow_state"):
                    session["workflow_state"] = result["workflow_state"]

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
                await _save_session(session_id, session)"""
graph_exec_new = """                # We still re-check mode but the whole mutation must be atomic
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
                    session = await _mutate_session(session_id, _apply_graph_result, allow_retry=False, customer_id=customer_id)
                    if session.get("mode") == "human":
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
                    raise e"""
content = content.replace(graph_exec_old, graph_exec_new)

# 6. Agent message
agent_msg_old = """                # Append to history
                session = await _get_session(session_id)
                session["conversation_history"].append({
                    "role": "agent",
                    "content": agent_message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "human_agent": True,
                    "agent_name": agent_name,
                })
                await _save_session(session_id, session)"""
agent_msg_new = """                # Append to history
                def _agent_message(s):
                    s["conversation_history"].append({
                        "role": "agent",
                        "content": agent_message,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "human_agent": True,
                        "agent_name": agent_name,
                    })
                    return True
                session = await _mutate_session(session_id, _agent_message, allow_retry=True)"""
content = content.replace(agent_msg_old, agent_msg_new)

# 7. Agent takeover
takeover_old = """                # Agent is taking over the conversation
                session = await _get_session(session_id)
                if session.get("mode") != "human":
                    session["mode"] = "human"
                    await _save_session(session_id, session)
                    
                    await manager.send_personal_message({
                        "type": "mode_change",
                        "mode": "human",
                        "agent_name": data.get("agent_name", "Support Agent"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }, session_id)"""
takeover_new = """                # Agent is taking over the conversation
                def _takeover(s):
                    if s.get("mode") != "human":
                        s["mode"] = "human"
                        return True
                    return False
                session = await _mutate_session(session_id, _takeover, allow_retry=True)
                if session and session.get("mode") == "human":
                    await manager.send_personal_message({
                        "type": "mode_change",
                        "mode": "human",
                        "agent_name": data.get("agent_name", "Support Agent"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }, session_id)"""
content = content.replace(takeover_old, takeover_new)

# 8. Agent release
release_old = """                # Agent is returning the conversation to the AI
                session = await _get_session(session_id)
                if session.get("mode") != "ai":
                    session["mode"] = "ai"
                    await _save_session(session_id, session)
                    
                    await manager.send_personal_message({
                        "type": "mode_change",
                        "mode": "ai",
                        "agent_name": data.get("agent_name", "AI Assistant"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }, session_id)"""
release_new = """                # Agent is returning the conversation to the AI
                def _release(s):
                    if s.get("mode") != "ai":
                        s["mode"] = "ai"
                        return True
                    return False
                session = await _mutate_session(session_id, _release, allow_retry=True)
                if session and session.get("mode") == "ai":
                    await manager.send_personal_message({
                        "type": "mode_change",
                        "mode": "ai",
                        "agent_name": data.get("agent_name", "AI Assistant"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }, session_id)"""
content = content.replace(release_old, release_new)

# 9. Agent disconnect
disconnect_old = """        # Safely return session to AI mode if it was abandoned in human mode
        session = await _get_session(session_id)
        # Check if there are still any other agents connected to this session
        has_other_agents = bool(manager.agent_connections.get(session_id))
        
        if session and session.get("mode") == "human" and not has_other_agents:
            session["mode"] = "ai"
            await _save_session(session_id, session)
            
            # Notify customer that AI has resumed
            await manager.send_personal_message({
                "type": "mode_change",
                "mode": "ai",
                "agent_name": "AI Assistant",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, session_id)"""
disconnect_new = """        # Safely return session to AI mode if it was abandoned in human mode
        # Check if there are still any other agents connected to this session
        has_other_agents = bool(manager.agent_connections.get(session_id))
        
        def _abandon(s):
            if s and s.get("mode") == "human" and not has_other_agents:
                s["mode"] = "ai"
                return True
            return False
            
        try:
            session = await _mutate_session(session_id, _abandon, allow_retry=True)
            if session and session.get("mode") == "ai" and not has_other_agents:
                # Notify customer that AI has resumed
                await manager.send_personal_message({
                    "type": "mode_change",
                    "mode": "ai",
                    "agent_name": "AI Assistant",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)
        except Exception as e:
            logger.error(f"Failed to abandon human mode for session {session_id}: {e}")"""
content = content.replace(disconnect_old, disconnect_new)

with open("app/websocket/chat_handler.py", "w") as f:
    f.write(content)
print("PATCHED")
