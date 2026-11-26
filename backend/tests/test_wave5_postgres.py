"""Integration checks for the Wave 5 RPCs on a disposable local PostgreSQL database."""

from contextlib import closing
from uuid import uuid4

import pytest

from tests.conftest import _is_safe_test_database_dsn

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
def cursor(test_postgres_dsn):
    if not _is_safe_test_database_dsn(test_postgres_dsn):
        pytest.skip("Wave 5 SQL tests require a disposable loopback PostgreSQL database")
    psycopg2 = pytest.importorskip("psycopg2")
    with closing(psycopg2.connect(test_postgres_dsn)) as conn, conn.cursor() as cur:
        try:
            yield cur
        finally:
            conn.rollback()


def test_persist_conversation_rejects_out_of_order_and_wrong_owner_writes(cursor):
    from psycopg2.extras import Json

    session = str(uuid4())
    transcript = [{"role": "customer", "content": "latest"}]
    rpc = "SELECT public.persist_conversation(%s, %s, %s, %s, false, now(), %s)"
    cursor.execute(rpc, (session, 1, Json(transcript), 12, None))
    cursor.execute(rpc, (session, 1, Json([]), 11, "2026-01-01T00:00:00Z"))
    cursor.execute(rpc, (session, 2, Json([]), 13, None))
    select_row = "SELECT customer_id, transcript, session_revision, ended_at FROM public.conversations WHERE session_id = %s"
    cursor.execute(select_row, (session,))
    assert cursor.fetchone() == (1, transcript, 12, None)
    cursor.execute(rpc, (session, 1, Json([]), 12, "2026-01-01T00:00:00Z"))
    cursor.execute(select_row, (session,))
    owner, contents, revision, ended_at = cursor.fetchone()
    assert (owner, contents, revision) == (1, transcript, 12)
    assert ended_at is not None


def test_ticket_statistics_counts_beyond_the_rest_page_size(cursor):
    cursor.execute("SELECT public.get_ticket_statistics()")
    before, = cursor.fetchone()
    cursor.execute("""
        INSERT INTO public.tickets (subject, description, status, type, priority, satisfaction_rating)
        SELECT 'Wave 5', 'Aggregate verification', 'open', 'inquiry', 'low', 5
        FROM generate_series(1, 1250)
    """)
    cursor.execute("SELECT public.get_ticket_statistics()")
    stats, = cursor.fetchone()
    assert stats["total_tickets"] == before["total_tickets"] + 1250
    assert stats["open_tickets"] == before["open_tickets"] + 1250
    assert stats["escalated_tickets"] == before["escalated_tickets"]
    for field, key in (("by_status", "open"), ("by_type", "inquiry"), ("by_priority", "low")):
        assert stats[field][key] == before[field].get(key, 0) + 1250
    cursor.execute("SELECT round(avg(satisfaction_rating)::numeric, 2) FROM public.tickets")
    expected_average, = cursor.fetchone()
    assert stats["avg_satisfaction"] == float(expected_average)
