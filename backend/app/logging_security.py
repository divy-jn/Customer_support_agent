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


# PII patterns (expanded) — matches common formats; may produce false positives on order IDs
PII_PATTERNS: dict[str, str] = {
    "credit_card": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",  #review: also matches 16-digit order/tracking numbers
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "phone_us": r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
    "phone_intl": r"\b\+\d{1,3}[-.\s]?\d{6,14}\b",
    "email": r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b",
    "ip_address": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
}

_TELEMETRY_PRIVATE_TEXT = re.compile(
    "|".join(f"(?:{pattern})" for pattern in PII_PATTERNS.values()),
    re.IGNORECASE,
)
_OTP_OR_SECRET = re.compile(
    r"(?i)\b(?:authorization\s*[:=]\s*bearer\s+|(?:api[_ -]?key|password|secret|(?:access[_ -]?)?token)\s*[:=]\s*)[^\s,;]+"
)


def redact_telemetry_text(value: str, *, limit: int = 200) -> str:
    """Redact complete matches before clipping a diagnostic preview."""
    if len(value) > 4096:
        return "[REDACTED: oversized text]"
    redacted = redact_sensitive_query(value)
    redacted = _OTP_OR_SECRET.sub("[REDACTED]", redacted)
    redacted = _TELEMETRY_PRIVATE_TEXT.sub("[REDACTED]", redacted)
    return " ".join(redacted.split())[:limit]


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
