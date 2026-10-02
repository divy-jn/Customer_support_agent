import os
import uuid
import psycopg2
import pytest
from datetime import datetime, timedelta, timezone

from app.outbox.reconciliation_coordinator import ReconciliationCoordinator
from app.config import settings

TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD", "postgres")
conn_string = f"dbname='postgres' user='postgres' host='localhost' port='5433' password='{TEST_DB_PASSWORD}'"

@pytest.fixture
def coordinator():
    return ReconciliationCoordinator(conn_string)

@pytest.fixture
def cleanup_db():
    yield
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM outbox_events WHERE recipient_address LIKE '%@repair.test'")
            cur.execute("DELETE FROM escalation_events WHERE customer_id >= 999000000")
            cur.execute("DELETE FROM session_escalation_state WHERE customer_id >= 999000000")
        conn.commit()

@pytest.fixture
def test_customer():
    # Use a high customer_id range for test isolation
    customer_id = 999000000 + int(uuid.uuid4().int % 100000)
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO customers (id, name, email) 
                VALUES (%s, 'Test Customer', %s)
                ON CONFLICT (id) DO NOTHING
            """, (customer_id, f"coord_{customer_id}@repair.test"))
        conn.commit()
    return customer_id

def test_discovery_pagination_and_ordering(coordinator, test_customer, cleanup_db):
    """Test bounded page size, deterministic ordering, and no duplicates across batches."""
    event_ids = []
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", ("disc_session", test_customer))
            # Insert 5 events with slight variations in created_at
            now = datetime.now(timezone.utc)
            for i in range(5):
                eid = str(uuid.uuid4())
                event_ids.append(eid)
                dt = now - timedelta(minutes=i)
                cur.execute("""
                    INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, client_request_id, payload_hash, created_at)
                    VALUES (%s, %s, %s, %s, 'hash', %s)
                """, (eid, "disc_session", test_customer, str(uuid.uuid4()), dt))
        conn.commit()
        
    res1, next_ca, next_eid = coordinator.discover_and_reconcile_escalations(horizon_days=7.0, batch_size=2)
    all_results = res1.results
    
    while next_ca is not None:
        res_next, next_ca, next_eid = coordinator.discover_and_reconcile_escalations(
            horizon_days=7.0, batch_size=2, last_created_at=next_ca, last_event_id=next_eid
        )
        all_results.extend(res_next.results)
    
    all_source_ids = [r.source_event_id for r in all_results]
    
    # Within our subset of 5 inserted event IDs, they should appear exactly twice (team, customer) 
    # across the entire pagination sweep.
    for eid in event_ids:
        count = all_source_ids.count(f"escalation:{eid}")
        assert count == 2, f"Event {eid} was processed {count} times (expected 2 for team+customer)"

def test_discovery_retention_window(coordinator, test_customer, cleanup_db):
    """Test that events older than the horizon are ignored."""
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", ("disc_session2", test_customer))
            # Insert 1 old event (8 days ago) and 1 new event (1 day ago)
            eid_old = str(uuid.uuid4())
            eid_new = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            
            cur.execute("""
                INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, client_request_id, payload_hash, created_at)
                VALUES (%s, %s, %s, %s, 'hash', %s)
            """, (eid_old, "disc_session2", test_customer, str(uuid.uuid4()), now - timedelta(days=8)))
            
            cur.execute("""
                INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, client_request_id, payload_hash, created_at)
                VALUES (%s, %s, %s, %s, 'hash', %s)
            """, (eid_new, "disc_session2", test_customer, str(uuid.uuid4()), now - timedelta(days=1)))
        conn.commit()
        
    res, _, _ = coordinator.discover_and_reconcile_escalations(horizon_days=7.0, batch_size=100)
    
    scanned_ids = [r.source_event_id for r in res.results]
    assert f"escalation:{eid_new}" in scanned_ids
    assert f"escalation:{eid_old}" not in scanned_ids

def test_startup_scheduler_disabled_mode():
    """Test that disabled mode skips startup."""
    import asyncio
    from app.outbox.lifecycle import start_reconciliation_scheduler, stop_reconciliation_scheduler
    import app.outbox.lifecycle as lc
    
    settings.reconciliation_enabled = False
    
    async def run():
        await start_reconciliation_scheduler()
        assert lc._reconciliation_task is None
        await stop_reconciliation_scheduler()
        
    asyncio.run(run())
    settings.reconciliation_enabled = True  # reset

def test_startup_scheduler_runs_and_stops():
    """Test that the scheduler starts up, creates a task, and stops gracefully."""
    import asyncio
    from app.outbox.lifecycle import start_reconciliation_scheduler, stop_reconciliation_scheduler
    import app.outbox.lifecycle as lc
    
    settings.reconciliation_enabled = True
    settings.reconciliation_interval_seconds = 1  # fast interval
    
    async def run():
        await start_reconciliation_scheduler()
        assert lc._reconciliation_task is not None
        assert not lc._reconciliation_task.done()
        
        # Give it a tiny bit of time to start its first sweep
        await asyncio.sleep(0.1)
        
        await stop_reconciliation_scheduler()
        assert lc._reconciliation_task is None
        
        
    asyncio.run(run())


def test_discovery_stable_sweep_boundary(coordinator, test_customer, cleanup_db):
    """Test that sweep_started_at acts as a stable upper bound for pagination."""
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", ("stable_session", test_customer))
            
            # Insert one event
            eid1 = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            cur.execute("""
                INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, client_request_id, payload_hash, created_at)
                VALUES (%s, %s, %s, %s, 'hash', %s)
            """, (eid1, "stable_session", test_customer, str(uuid.uuid4()), now - timedelta(seconds=10)))
        conn.commit()

    # Start a sweep with T0
    T0 = datetime.now(timezone.utc)
    
    # Before we fetch page 1, insert another event newer than T0
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            eid2 = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, client_request_id, payload_hash, created_at)
                VALUES (%s, %s, %s, %s, 'hash', %s)
            """, (eid2, "stable_session", test_customer, str(uuid.uuid4()), datetime.now(timezone.utc)))
        conn.commit()

    # Execute page 1 using T0
    res1, next_ca, next_eid = coordinator.discover_and_reconcile_escalations(
        horizon_days=7.0, batch_size=10, sweep_started_at=T0
    )
    
    scanned_ids = [r.source_event_id for r in res1.results]
    
    # eid1 was before T0, so it should be included
    assert f"escalation:{eid1}" in scanned_ids
    # eid2 was after T0, so it MUST NOT be included
    assert f"escalation:{eid2}" not in scanned_ids


def test_async_event_loop_not_blocked(monkeypatch):
    """Verify that synchronous psycopg2 operations don't block the FastAPI event loop."""
    import asyncio
    import threading
    from app.outbox.lifecycle import _reconciliation_loop
    from unittest.mock import MagicMock
    
    settings.reconciliation_enabled = True
    settings.reconciliation_startup_pages = 1
    
    # Patch the coordinator so we can intercept the call and check the thread
    called_thread_id = None
    original_discover = ReconciliationCoordinator.discover_and_reconcile_escalations
    
    def mock_discover(self, *args, **kwargs):
        nonlocal called_thread_id
        called_thread_id = threading.get_ident()
        return original_discover(self, *args, **kwargs)
        
    monkeypatch.setattr(ReconciliationCoordinator, "discover_and_reconcile_escalations", mock_discover)
    
    import app.outbox.lifecycle as lc
    monkeypatch.setattr(lc, "_build_dsn", lambda: conn_string)
    
    async def run():
        main_thread_id = threading.get_ident()
        
        # We must initialize the locks
        import app.outbox.lifecycle as lc
        lc._reconciliation_cancel_event = asyncio.Event()
        lc._reconciliation_lock = asyncio.Lock()
            
        lc._reconciliation_cancel_event.clear()
        
        # Run the loop which should perform 1 page sweep
        task = asyncio.create_task(lc._reconciliation_loop())
        # Let it do the startup sweep
        await asyncio.sleep(0.5)
        
        # Stop it
        lc._reconciliation_cancel_event.set()
        await task
        
        return main_thread_id
        
    main_id = asyncio.run(run())
    
    # The discover method must have been called
    assert called_thread_id is not None
    # And it MUST NOT have been called on the main asyncio thread
    assert called_thread_id != main_id


def test_periodic_cursor_continuation(coordinator, test_customer, cleanup_db, monkeypatch):
    """
    Verify that if a periodic sweep hits the 50-page max bound, the next periodic iteration
    resumes from the continuation cursor, maintains the same sweep_started_at,
    and reaches older eligible events.
    """
    import asyncio
    from app.outbox.lifecycle import _reconciliation_loop
    import app.outbox.lifecycle as lc
    
    settings.reconciliation_enabled = True
    settings.reconciliation_startup_pages = 0  # Skip startup sweep
    settings.reconciliation_batch_size = 2     # Small batch size to hit page limits quickly
    settings.reconciliation_interval_seconds = 0.01  # Fast iterations
    
    # We want to test reaching >50 pages.
    # We'll insert 105 events. With batch_size=2, this requires 53 pages.
    # The first periodic iteration will process 50 pages (100 events).
    # The second periodic iteration should process 3 pages (5 events) and reset.
    
    event_ids = []
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM outbox_events WHERE recipient_address LIKE '%@repair.test'")
            cur.execute("DELETE FROM escalation_events WHERE customer_id >= 999000000")
            cur.execute("DELETE FROM session_escalation_state WHERE customer_id >= 999000000")
            
            cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", ("cursor_session", test_customer))
            
            now = datetime.now(timezone.utc)
            # Insert from oldest to newest so they appear in reverse order (newest first) during DESC keyset pagination
            for i in range(105):
                eid = str(uuid.uuid4())
                event_ids.append(eid)
                dt = now - timedelta(seconds=(105 - i))
                cur.execute("""
                    INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, client_request_id, payload_hash, created_at)
                    VALUES (%s, %s, %s, %s, 'hash', %s)
                """, (eid, "cursor_session", test_customer, str(uuid.uuid4()), dt))
        conn.commit()
        
    # We need to intercept discover_and_reconcile_escalations to spy on arguments
    spy_calls = []
    original_discover = ReconciliationCoordinator.discover_and_reconcile_escalations
    
    def mock_discover(self, *args, **kwargs):
        # Record the cursor arguments
        spy_calls.append({
            'last_created_at': kwargs.get('last_created_at'),
            'last_event_id': kwargs.get('last_event_id'),
            'sweep_started_at': kwargs.get('sweep_started_at')
        })
        return original_discover(self, *args, **kwargs)
        
    monkeypatch.setattr(ReconciliationCoordinator, "discover_and_reconcile_escalations", mock_discover)
    monkeypatch.setattr(lc, "_build_dsn", lambda: conn_string)
    
    async def run():
        lc._reconciliation_cancel_event = asyncio.Event()
        lc._reconciliation_lock = asyncio.Lock()
            
        lc._reconciliation_cancel_event.clear()
        
        task = asyncio.create_task(lc._reconciliation_loop())
        # Let it run for enough time to complete multiple periodic iterations (53+ DB queries)
        # We give it 30 seconds because hitting Postgres 53+ times can take >10 seconds on Windows.
        await asyncio.sleep(30.0)
        
        lc._reconciliation_cancel_event.set()
        await task

    asyncio.run(run())
    
    # Analyze the spy calls
    # We expect 53 calls total before it exhausts (50 in iter 1, 3 in iter 2)
    # The first call in iter 1 should have last_created_at=None
    # The 51st call (first call in iter 2) MUST NOT have last_created_at=None
    # It must carry over the cursor from call 50.
    # sweep_started_at must be identical for all 53 calls.
    # Call 54 (start of a NEW sweep after exhaustion) should have last_created_at=None again.
    
    assert len(spy_calls) >= 53, f"Not enough pages processed (got {len(spy_calls)})"
    
    first_sweep_time = spy_calls[0]['sweep_started_at']
    assert first_sweep_time is not None
    assert spy_calls[0]['last_created_at'] is None
    
    # Find the boundary where the sweep resets
    reset_index = -1
    for i in range(1, len(spy_calls)):
        if spy_calls[i]['sweep_started_at'] != first_sweep_time:
            reset_index = i
            break
            
    assert reset_index > 50, f"Sweep reset too early, expected >50, got {reset_index}"
    
    # All calls before reset_index (except index 0) must have a cursor and the same sweep time
    for i in range(1, reset_index):
        assert spy_calls[i]['last_created_at'] is not None, f"Cursor reset prematurely at index {i}!"
        assert spy_calls[i]['sweep_started_at'] == first_sweep_time, f"Sweep time changed prematurely at index {i}!"
        
    # The first call of the NEW sweep must have a None cursor and a new sweep time
    assert spy_calls[reset_index]['last_created_at'] is None, "Cursor did not reset after exhaustion"
    assert spy_calls[reset_index]['sweep_started_at'] > first_sweep_time, "Sweep time did not advance for new sweep"
