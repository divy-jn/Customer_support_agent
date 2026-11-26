"""Live session cache with conditional revision updates."""
from copy import deepcopy
import json
import logging
import asyncio
from enum import Enum
from app.config import settings

logger = logging.getLogger(__name__)

class SaveResult(Enum):
    SUCCESS = 0
    MISSING = 1
    CONFLICT = 2

# Conditional Redis import — falls back to in-memory if not installed
try:
    from upstash_redis.asyncio import Redis as UpstashRedis
    _redis_available = True
except ImportError:
    _redis_available = False

# Initialize Upstash Redis if configured AND available
redis_client = None
if _redis_available and settings.upstash_redis_url and settings.upstash_redis_token:
    redis_client = UpstashRedis(url=settings.upstash_redis_url, token=settings.upstash_redis_token)
else:
    if not _redis_available:
        logger.warning("upstash-redis not installed. Using in-memory session store.")
    else:
        logger.warning("Upstash Redis not configured. Using in-memory store.")

CAS_LUA_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
    return 1 -- MISSING
end
local decoded = cjson.decode(current)
local current_rev = decoded['_session_revision']
if current_rev == nil then current_rev = 1 end
local expected_rev = tonumber(ARGV[1])
if current_rev ~= expected_rev then
    return 2 -- CONFLICT
end
redis.call('SET', KEYS[1], ARGV[2], 'EX', tonumber(ARGV[3]))
return 0 -- SUCCESS
"""

CREATE_LUA_SCRIPT = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 1 then
    return 2 -- CONFLICT
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[2]))
return 0 -- SUCCESS
"""

class SessionStore(dict):
    def __init__(self):
        super().__init__()
        self._lock = asyncio.Lock()

    async def get_session(self, session_id: str) -> dict | None:
        """Load session blob."""
        if redis_client:
            data = await redis_client.get(f"session:{session_id}")
            if data:
                return json.loads(data) if isinstance(data, str) else data
            return None
        else:
            async with self._lock:
                data = self.get(session_id)
                return deepcopy(data) if data else None

    async def get_sessions(self, session_ids: list[str]) -> list[dict | None]:
        """Load values for a known set of live IDs in the same order."""
        if not session_ids:
            return []
        if redis_client:
            values = await redis_client.mget(*(f"session:{session_id}" for session_id in session_ids))
            return [json.loads(value) if isinstance(value, str) else value for value in values]
        async with self._lock:
            return [deepcopy(self.get(session_id)) for session_id in session_ids]

    async def save_session(self, session_id: str, session: dict) -> None:
        """Unconditional save. Overwrites any existing session."""
        # #review: Unconditional save can overwrite concurrent mutations; save_session_conditional should be preferred
        if redis_client:
            await redis_client.set(f"session:{session_id}", json.dumps(session), ex=86400)
        else:
            async with self._lock:
                import copy
                self[session_id] = copy.deepcopy(session)

    async def create_session_if_absent(self, session_id: str, session: dict) -> SaveResult:
        """Create session if it does not already exist."""
        if redis_client:
            payload = json.dumps(session)
            result = await redis_client.eval(
                CREATE_LUA_SCRIPT,
                keys=[f"session:{session_id}"],
                args=[payload, 86400]
            )
            return SaveResult(result)
        else:
            async with self._lock:
                if session_id in self:
                    return SaveResult.CONFLICT
                import copy
                self[session_id] = copy.deepcopy(session)
                return SaveResult.SUCCESS


    async def save_session_conditional(self, session_id: str, session: dict, expected_revision: int) -> SaveResult:
        """
        Save this complete session record atomically only if the previously observed 
        revision matches expected_revision.
        """
        # Increment the revision for the new payload
        new_revision = expected_revision + 1
        session["_session_revision"] = new_revision

        if redis_client:
            payload = json.dumps(session)
            # upstash_redis eval takes keys and args
            result = await redis_client.eval(
                CAS_LUA_SCRIPT,
                keys=[f"session:{session_id}"],
                args=[expected_revision, payload, 86400]
            )
            return SaveResult(result)
        else:
            async with self._lock:
                current = self.get(session_id)
                if current is None:
                    return SaveResult.MISSING
                current_rev = current.get("_session_revision", 1)
                if current_rev != expected_revision:
                    return SaveResult.CONFLICT
                import copy
                self[session_id] = copy.deepcopy(session)
                return SaveResult.SUCCESS


session_store = SessionStore()

