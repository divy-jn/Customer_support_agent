import logging

from app.logging_security import QuerySecretRedactionFilter, redact_sensitive_query


def test_redact_sensitive_query_values_but_keep_non_sensitive_query_fields():
    value = "/ws/chat?token=customer-jwt-secret&session_id=session-12&agent_secret=static-key"

    redacted = redact_sensitive_query(value)

    assert "customer-jwt-secret" not in redacted
    assert "static-key" not in redacted
    assert "token=[REDACTED]" in redacted
    assert "agent_secret=[REDACTED]" in redacted
    assert "session_id=session-12" in redacted


def test_logging_filter_redacts_access_log_format_args_and_structured_url():
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1", "GET", "/ws/chat?token=jwt-secret", "1.1", 101),
        exc_info=None,
    )
    record.path = "wss://example.test/ws/chat?agent_secret=private-key"

    assert QuerySecretRedactionFilter().filter(record) is True
    assert "jwt-secret" not in str(record.args)
    assert "private-key" not in record.path
