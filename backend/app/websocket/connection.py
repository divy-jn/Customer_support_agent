"""
WebSocket Connection Manager — handles real-time connections for the customer chat widget.

Features:
- Heartbeat/ping to detect stale connections
- Connection count metrics
- Max connections per session limit
"""

import asyncio
import json
import logging
from typing import Dict, List

from fastapi import WebSocket

from app.config import settings

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  Connection Metrics
# ──────────────────────────────────────────────
_connection_metrics = {
    "total_customer_connects": 0,
    "total_customer_disconnects": 0,
    "total_agent_connects": 0,
    "total_agent_disconnects": 0,
}


def get_connection_metrics() -> dict:
    """Return current connection metrics for the admin dashboard."""
    return {
        **_connection_metrics,
        "active_customer_sessions": len(manager.active_connections),
        "active_agent_sessions": sum(len(agents) for agents in manager.agent_connections.values()),
    }


class ConnectionManager:
    def __init__(self):
        # Maps session_id -> WebSocket
        self.active_connections: Dict[str, WebSocket] = {}
        # Maps session_id -> List of active agent WebSockets (for dashboard monitoring/takeover)
        self.agent_connections: Dict[str, List[WebSocket]] = {}
        # Heartbeat tasks per session_id
        self._heartbeat_tasks: Dict[str, asyncio.Task] = {}

    async def connect_customer(self, websocket: WebSocket, session_id: str):
        """Accept a customer's WebSocket connection."""
        await websocket.accept()
        self.active_connections[session_id] = websocket
        _connection_metrics["total_customer_connects"] += 1

        logger.info(
            f"Customer connected: {session_id}",
            extra={
                "session_id": session_id,
                "active_customers": len(self.active_connections),
            },
        )

        # Start heartbeat for this connection
        self._start_heartbeat(session_id)

    async def connect_agent(self, websocket: WebSocket, session_id: str):
        """Accept an agent's WebSocket connection for monitoring."""
        if session_id not in self.agent_connections:
            self.agent_connections[session_id] = []

        # Enforce max agent connections per session
        if len(self.agent_connections[session_id]) >= settings.ws_max_connections_per_session:
            logger.warning(
                f"Max agent connections reached for session: {session_id}",
                extra={"session_id": session_id, "max": settings.ws_max_connections_per_session},
            )
            await websocket.close(code=1008, reason="Max agent connections reached")
            return

        await websocket.accept()
        self.agent_connections[session_id].append(websocket)
        _connection_metrics["total_agent_connects"] += 1

        logger.info(
            f"Agent connected to monitor session: {session_id}",
            extra={
                "session_id": session_id,
                "agent_count": len(self.agent_connections[session_id]),
            },
        )

    def disconnect_customer(self, session_id: str):
        """Remove a customer's connection."""
        if session_id in self.active_connections:
            del self.active_connections[session_id]
            _connection_metrics["total_customer_disconnects"] += 1

            # Cancel heartbeat
            self._cancel_heartbeat(session_id)

            logger.info(
                f"Customer disconnected: {session_id}",
                extra={
                    "session_id": session_id,
                    "active_customers": len(self.active_connections),
                },
            )

    def disconnect_agent(self, websocket: WebSocket, session_id: str):
        """Remove an agent's connection."""
        if session_id in self.agent_connections:
            if websocket in self.agent_connections[session_id]:
                self.agent_connections[session_id].remove(websocket)
                _connection_metrics["total_agent_disconnects"] += 1
            if not self.agent_connections[session_id]:
                del self.agent_connections[session_id]
            logger.info(
                f"Agent disconnected from session: {session_id}",
                extra={"session_id": session_id},
            )

    async def send_personal_message(self, message: dict, session_id: str):
        """Send a message directly to a specific customer."""
        websocket = self.active_connections.get(session_id)
        if websocket:
            try:
                await websocket.send_text(json.dumps(message))
            except Exception as e:
                logger.error(
                    f"Error sending message to {session_id}: {e}",
                    extra={"session_id": session_id, "error": str(e)},
                )
                self.disconnect_customer(session_id)

    async def broadcast_to_agents(self, message: dict, session_id: str):
        """Broadcast a message to all agents monitoring this session."""
        if session_id in self.agent_connections:
            dead_connections = []
            for agent_ws in self.agent_connections[session_id]:
                try:
                    await agent_ws.send_text(json.dumps(message))
                except Exception:
                    dead_connections.append(agent_ws)

            for dead_ws in dead_connections:
                self.disconnect_agent(dead_ws, session_id)

    # ──────────────────────────────────────────
    #  Heartbeat / Ping-Pong
    # ──────────────────────────────────────────
    def _start_heartbeat(self, session_id: str):
        """Start periodic ping for a customer connection."""
        if session_id in self._heartbeat_tasks:
            self._heartbeat_tasks[session_id].cancel()

        task = asyncio.create_task(self._heartbeat_loop(session_id))
        self._heartbeat_tasks[session_id] = task

    def _cancel_heartbeat(self, session_id: str):
        """Cancel the heartbeat task for a session."""
        task = self._heartbeat_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()

    async def _heartbeat_loop(self, session_id: str):
        """Send periodic pings to detect stale connections."""
        interval = settings.ws_heartbeat_interval
        try:
            while session_id in self.active_connections:
                await asyncio.sleep(interval)
                ws = self.active_connections.get(session_id)
                if ws:
                    try:
                        await ws.send_text(json.dumps({"type": "ping", "ts": "heartbeat"}))
                    except Exception:
                        logger.info(
                            f"Heartbeat failed, removing stale connection: {session_id}",
                            extra={"session_id": session_id},
                        )
                        self.disconnect_customer(session_id)
                        break
        except asyncio.CancelledError:
            pass


manager = ConnectionManager()
