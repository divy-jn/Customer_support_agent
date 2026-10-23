"""Customer-scoped WebSocket rate limiting.

Uses an atomic Redis sliding window when the configured Upstash session store is
available. A process-local fallback is retained for development and for graceful
operation during a Redis outage; it is still shared across connections in that process.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from typing import Deque

from app.persistence import session as session_persistence

logger = logging.getLogger(__name__)

_REDIS_SLIDING_WINDOW_LUA = """
local now = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local member = ARGV[4]
local key = KEYS[1]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window_ms)
local count = redis.call('ZCARD', key)
if count >= max_requests then
    return 0
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window_ms + 1000)
return 1
"""

_LOCAL_EVENTS: dict[str, Deque[float]] = {}
_LOCAL_LOCK = threading.Lock()
_LOCAL_CALLS = 0


def _consume_local_rate_limit(
    identity: str,
    *,
    limit: int,
    window_seconds: int,
    now: float | None = None,
) -> bool:
    """Atomic in-process fallback used when Redis is not configured or unavailable."""
    global _LOCAL_CALLS
    now = time.monotonic() if now is None else now
    cutoff = now - window_seconds
    with _LOCAL_LOCK:
        events = _LOCAL_EVENTS.setdefault(identity, deque())
        while events and events[0] <= cutoff:
            events.popleft()
        allowed = len(events) < limit
        if allowed:
            events.append(now)

        _LOCAL_CALLS += 1
        # Bound memory for customers that never return after their window expires.
        if _LOCAL_CALLS % 256 == 0:
            for key, prior_events in list(_LOCAL_EVENTS.items()):
                while prior_events and prior_events[0] <= cutoff:
                    prior_events.popleft()
                if not prior_events:
                    _LOCAL_EVENTS.pop(key, None)
        return allowed


async def consume_customer_message(
    identity: str,
    *,
    limit: int,
    window_seconds: int = 60,
) -> bool:
    """Consume one message from an identity's shared sliding-window budget."""
    limit = max(1, int(limit))
    window_seconds = max(1, int(window_seconds))
    redis_client = session_persistence.redis_client
    if redis_client is not None:
        now_ms = int(time.time() * 1000)
        key = f"rate-limit:ws:{identity}"
        try:
            result = await redis_client.eval(
                _REDIS_SLIDING_WINDOW_LUA,
                keys=[key],
                args=[now_ms, window_seconds * 1000, limit, f"{now_ms}:{uuid.uuid4().hex}"],
            )
            return int(result) == 1
        except Exception:
            logger.warning(
                "Shared WebSocket rate limiter unavailable; falling back to process-local budget",
                exc_info=True,
                extra={"rate_limit_identity": identity},
            )

    return _consume_local_rate_limit(
        identity,
        limit=limit,
        window_seconds=window_seconds,
    )
