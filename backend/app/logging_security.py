"""Log-field redaction for sensitive query-string credentials."""
from __future__ import annotations

import logging
import re
from typing import Any

# Match only credential-like query keys, retaining the name and delimiters.
_SENSITIVE_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:token|access_token|refresh_token|agent_secret|api_key|x_api_key|secret|password|authorization)=)[^&#\s\"'<>]*"
)


def redact_sensitive_query(value: str) -> str:
    """Redact credential values in URL or request-line text while preserving other query fields."""
    return _SENSITIVE_QUERY_VALUE.sub(r"\1[REDACTED]", value)


def _redact_nested(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_query(value)
    if isinstance(value, tuple):
        return tuple(_redact_nested(item) for item in value)
    if isinstance(value, list):
        return [_redact_nested(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_nested(item) for key, item in value.items()}
    return value


class QuerySecretRedactionFilter(logging.Filter):
    """Sanitize log message templates, formatting args and structured extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _redact_nested(record.msg)
            record.args = _redact_nested(record.args)
            # This also covers application extras such as request.url stored as path.
            for key, value in tuple(record.__dict__.items()):
                if key not in {"msg", "args"}:
                    record.__dict__[key] = _redact_nested(value)
        except Exception:
            # Logging must not become an application failure if a custom value is unusual.
            return True
        return True
