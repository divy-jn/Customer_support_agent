import pytest

from app.websocket import rate_limiter


def test_sliding_window_limit_is_shared_across_reconnections(monkeypatch):
    monkeypatch.setattr(rate_limiter.session_persistence, "redis_client", None)
    with rate_limiter._LOCAL_LOCK:
        rate_limiter._LOCAL_EVENTS.clear()

    # Different sockets/sessions for the same authenticated customer share one key.
    assert rate_limiter._consume_local_rate_limit("customer:42", limit=2, window_seconds=60, now=100.0)
    assert rate_limiter._consume_local_rate_limit("customer:42", limit=2, window_seconds=60, now=101.0)
    assert not rate_limiter._consume_local_rate_limit("customer:42", limit=2, window_seconds=60, now=102.0)
    assert rate_limiter._consume_local_rate_limit("customer:99", limit=2, window_seconds=60, now=102.0)


def test_sliding_window_expires_old_requests(monkeypatch):
    monkeypatch.setattr(rate_limiter.session_persistence, "redis_client", None)
    with rate_limiter._LOCAL_LOCK:
        rate_limiter._LOCAL_EVENTS.clear()

    assert rate_limiter._consume_local_rate_limit("customer:17", limit=1, window_seconds=60, now=100.0)
    assert not rate_limiter._consume_local_rate_limit("customer:17", limit=1, window_seconds=60, now=120.0)
    assert rate_limiter._consume_local_rate_limit("customer:17", limit=1, window_seconds=60, now=160.0)


@pytest.mark.asyncio
async def test_configured_redis_rate_limiter_uses_atomic_script(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.calls = []

        async def eval(self, script, *, keys, args):
            self.calls.append((script, keys, args))
            return 1

    fake = FakeRedis()
    monkeypatch.setattr(rate_limiter.session_persistence, "redis_client", fake)
    allowed = await rate_limiter.consume_customer_message("customer:8", limit=3)

    assert allowed is True
    assert len(fake.calls) == 1
    script, keys, args = fake.calls[0]
    assert "ZREMRANGEBYSCORE" in script
    assert keys == ["rate-limit:ws:customer:8"]
    assert args[2] == 3
