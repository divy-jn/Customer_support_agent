import os
import re
import urllib.parse
import pytest

def get_test_postgres_dsn() -> str | None:
    """Return the test PostgreSQL connection string if explicitly configured, else None.

    Prefers TEST_POSTGRES_URL if provided, then constructs DSN from
    TEST_DB_PASSWORD along with TEST_DB_HOST, TEST_DB_PORT, TEST_DB_USER, TEST_DB_NAME.
    Never defaults TEST_DB_PASSWORD to a hardcoded string, preventing accidental connections.
    """
    url = os.environ.get("TEST_POSTGRES_URL")
    if url:
        return url
    password = os.environ.get("TEST_DB_PASSWORD")
    if password:
        host = os.environ.get("TEST_DB_HOST", "localhost")
        port = os.environ.get("TEST_DB_PORT", "5433")
        user = os.environ.get("TEST_DB_USER", "postgres")
        dbname = os.environ.get("TEST_DB_NAME", "postgres")
        return f"dbname='{dbname}' user='{user}' host='{host}' port='{port}' password='{password}'"
    return None

def pytest_configure(config):
    config.addinivalue_line(
        "markers", 
        "llm_eval: marks tests that require a live LLM (deselect with '-m \"not llm_eval\"')"
    )
    config.addinivalue_line(
        "markers",
        "requires_postgres: marks tests that require a live PostgreSQL test database"
    )

# ── Deterministic LLM gating ──────────────────────────────
# A session-scoped probe checks whether the configured LLM provider
# accepts authentication. If the probe fails with a 401/auth error,
# all @pytest.mark.llm_eval tests are automatically skipped with an
# explicit reason.
#
# IMPORTANT: Only authentication/credential failures trigger a skip.
# Timeouts, DNS errors, malformed responses, and application bugs
# must NOT be silently converted into skips — they propagate as
# real test failures.

_llm_available = None
_llm_skip_reason = None

def _probe_llm_connectivity() -> bool:
    """One-shot probe: can we reach the configured LLM without auth errors?"""
    global _llm_available, _llm_skip_reason
    if _llm_available is not None:
        return _llm_available
    try:
        from app.llm_factory import get_llm
        llm = get_llm(temperature=0)
        # Minimal probe — a single token completion
        llm.invoke("Say OK")
        _llm_available = True
    except Exception as e:
        err_str = str(e).lower()
        err_type = type(e).__name__.lower()
        # Only skip on clear authentication/credential failures
        is_auth_failure = (
            "401" in err_str
            or "unauthorized" in err_str
            or "authentication" in err_type
            or "authenticationerror" in err_type
        )
        if is_auth_failure:
            _llm_available = False
            _llm_skip_reason = f"LLM authentication failed: {type(e).__name__}: {e}"
        else:
            # Non-auth errors must NOT be silently skipped.
            # Mark LLM as available so the test runs and the real error surfaces.
            _llm_available = True
    return _llm_available

def pytest_runtest_setup(item):
    """Auto-skip llm_eval tests when the LLM provider rejects authentication,
    and auto-skip requires_postgres tests when no real test database is configured."""
    markers = list(item.iter_markers(name="llm_eval"))
    if markers:
        if not _probe_llm_connectivity():
            pytest.skip(_llm_skip_reason or "LLM authentication failed — skipping live LLM eval")

    pg_markers = list(item.iter_markers(name="requires_postgres"))
    if pg_markers:
        if not get_test_postgres_dsn():
            pytest.skip("Real PostgreSQL test database required (set TEST_POSTGRES_URL or TEST_DB_PASSWORD).")


@pytest.fixture
def test_postgres_dsn():
    """Return the configured test PostgreSQL DSN or skip the test."""
    dsn = get_test_postgres_dsn()
    if not dsn:
        pytest.skip("Real PostgreSQL test database required (set TEST_POSTGRES_URL or TEST_DB_PASSWORD).")
    return dsn


@pytest.fixture(autouse=True)
def configure_test_approval_signing_secret(monkeypatch):
    """Ensure a dedicated test APPROVAL_SIGNING_SECRET is available for unit tests."""
    from app.config import settings
    if not getattr(settings, "approval_signing_secret", None):
        monkeypatch.setattr(settings, "approval_signing_secret", "test-approval-signing-secret-key-32chars")


def _is_safe_test_database_dsn(dsn: str) -> bool:
    """Positively restrict fixture seeding to approved local loopback hosts (127.0.0.1, localhost, ::1).

    Guards against accidental execution against remote, staging, or production databases
    by strictly validating the parsed host rather than relying on hostname blocklists.
    """
    if not dsn or not isinstance(dsn, str):
        return False

    ALLOWED_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

    # 1. URL format (postgresql:// or postgres://)
    if dsn.startswith("postgresql://") or dsn.startswith("postgres://"):
        try:
            parsed = urllib.parse.urlparse(dsn)
            hostname = (parsed.hostname or "").strip().lower()
            return hostname in ALLOWED_LOOPBACK_HOSTS
        except Exception:
            return False

    # 2. libpq key=value format (host=... or host='...')
    host_match = re.search(r"\bhost\s*=\s*['\"]?([^'\"\s;]+)['\"]?", dsn, re.IGNORECASE)
    if host_match:
        hostname = host_match.group(1).strip().lower()
        return hostname in ALLOWED_LOOPBACK_HOSTS

    return False


@pytest.fixture(scope="session", autouse=True)
def ensure_test_postgres_baseline():
    """Ensure baseline test records exist whenever a real local test database is configured.

    This bootstrap is restricted strictly to local disposable test databases and will
    refuse to execute against remote, shared, or production databases.
    """
    dsn = get_test_postgres_dsn()
    if not dsn or not _is_safe_test_database_dsn(dsn):
        return
    try:
        import psycopg2
        with psycopg2.connect(dsn) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO customers (id, name, email) VALUES
                        (1, 'Test Customer One', 'customer1@test.local'),
                        (2, 'Test Customer Two', 'customer2@test.local'),
                        (3, 'Test Customer Three', 'customer3@test.local'),
                        (4, 'Test Customer Four', 'customer4@test.local'),
                        (5, 'Test Customer Five', 'customer5@test.local'),
                        (6, 'Test Customer Six', 'customer6@test.local'),
                        (10, 'Test Customer Ten', 'customer10@test.local'),
                        (999, 'Test Customer 999', 'customer999@test.local'),
                        (9999, 'Test Customer 9999', 'customer9999@test.local')
                    ON CONFLICT (id) DO NOTHING;
                """)
                cur.execute("""
                    INSERT INTO products (id, name, category, price, stock, description) VALUES
                        (1, 'Test Product', 'General', 100.00, 50, 'Standard test product')
                    ON CONFLICT (id) DO NOTHING;
                """)
                cur.execute("""
                    INSERT INTO orders (id, customer_id, product_id, status, tracking_number) VALUES
                        (888, 1, 1, 'cancelled', 'TRK-888'),
                        (999, 1, 1, 'active', 'TRK-999'),
                        (1001, 1, 1, 'active', 'TRK-1001'),
                        (4004, 1, 1, 'delivered', 'TRK-4004')
                    ON CONFLICT (id) DO NOTHING;
                """)
                cur.execute("""
                    SELECT setval(pg_get_serial_sequence('customers', 'id'), coalesce(max(id), 1)) FROM customers;
                    SELECT setval(pg_get_serial_sequence('products', 'id'), coalesce(max(id), 1)) FROM products;
                    SELECT setval(pg_get_serial_sequence('orders', 'id'), coalesce(max(id), 1)) FROM orders;
                """)
    except Exception:
        # Ignore if tables are not yet created or connection is transient
        pass
