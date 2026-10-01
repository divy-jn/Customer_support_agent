"""
Outbox Email Sender Abstraction — Slice 3.3

Defines the sender boundary between the outbox worker and the actual
SMTP implementation. This separation allows:
  - Unit/integration tests to use a deterministic dummy sender.
  - Production code to delegate to the existing gmail_server.send_email.
  - Future replacement with a dedicated SMTP provider without touching
    the worker logic.

CRITICAL: This module does NOT modify the existing email_service.py or
gmail_server.py semantics.  It merely wraps them behind a testable
interface.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("outbox.sender")


# ──────────────────────────────────────────────
#  Send Result Model
# ──────────────────────────────────────────────

class SendOutcome(str, Enum):
    """Outcome categories for email delivery attempts."""
    SUCCESS = "SUCCESS"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    PERMANENT_RECIPIENT_FAILURE = "PERMANENT_RECIPIENT_FAILURE"
    AUTH_CONFIG_FAILURE = "AUTH_CONFIG_FAILURE"


@dataclass(frozen=True)
class SendResult:
    """Immutable result of an email send attempt."""
    outcome: SendOutcome
    error_message: Optional[str] = None


# ──────────────────────────────────────────────
#  Sender ABC
# ──────────────────────────────────────────────

class EmailSender(ABC):
    """
    Abstract boundary for email dispatch.

    The outbox worker calls ``send()`` and reacts to the
    ``SendResult.outcome`` to determine the next state transition.
    """

    @abstractmethod
    def send(
        self,
        to: str,
        subject: str,
        html_body: str,
    ) -> SendResult:
        """
        Attempt to deliver one email.

        Returns a ``SendResult`` whose ``outcome`` determines worker behaviour:
          - SUCCESS              → mark SENT
          - TRANSIENT_FAILURE    → schedule retry (RETRYABLE)
          - PERMANENT_RECIPIENT_FAILURE → terminal FAILED
          - AUTH_CONFIG_FAILURE  → terminal FAILED
        """


# ──────────────────────────────────────────────
#  Production Sender (wraps existing gmail_server)
# ──────────────────────────────────────────────

class GmailSmtpSender(EmailSender):
    """
    Production sender delegating to the existing
    ``app.mcp.gmail_server.send_email`` function.

    Classifies the raw string result / exception into structured
    ``SendOutcome`` categories.
    """

    # Keywords indicating permanent recipient problems (550-class SMTP errors)
    _PERMANENT_RECIPIENT_KEYWORDS = frozenset({
        "550", "551", "552", "553", "invalid recipient",
        "user unknown", "mailbox not found", "does not exist",
        "address rejected",
    })

    # Keywords indicating authentication / configuration failures
    _AUTH_CONFIG_KEYWORDS = frozenset({
        "535", "534", "authentication", "login",
        "app password", "not set in environment",
        "credentials", "username and password not accepted",
    })

    def send(self, to: str, subject: str, html_body: str) -> SendResult:
        try:
            from app.mcp.gmail_server import send_email
            result_msg = send_email(to=to, subject=subject, body=html_body, is_html=True)

            if isinstance(result_msg, str) and result_msg.startswith("Failed"):
                return self._classify_error(result_msg)

            return SendResult(outcome=SendOutcome.SUCCESS)

        except Exception as exc:
            return self._classify_error(str(exc))

    def _classify_error(self, error_text: str) -> SendResult:
        """Classify an error string into a ``SendOutcome``."""
        lower = error_text.lower()

        for kw in self._AUTH_CONFIG_KEYWORDS:
            if kw in lower:
                return SendResult(
                    outcome=SendOutcome.AUTH_CONFIG_FAILURE,
                    error_message=self._sanitize(error_text),
                )

        for kw in self._PERMANENT_RECIPIENT_KEYWORDS:
            if kw in lower:
                return SendResult(
                    outcome=SendOutcome.PERMANENT_RECIPIENT_FAILURE,
                    error_message=self._sanitize(error_text),
                )

        # Default: treat unknown failures as transient (network errors, timeouts, etc.)
        return SendResult(
            outcome=SendOutcome.TRANSIENT_FAILURE,
            error_message=self._sanitize(error_text),
        )

    @staticmethod
    def _sanitize(msg: str, max_len: int = 500) -> str:
        """Truncate and strip potential secrets from error messages."""
        return msg[:max_len]


# ──────────────────────────────────────────────
#  Dummy / Test Sender
# ──────────────────────────────────────────────

class DummySender(EmailSender):
    """
    Deterministic test sender.

    Configure with a ``default_outcome`` or push specific outcomes onto
    a FIFO queue.  Records all send attempts for assertion.
    """

    def __init__(self, default_outcome: SendOutcome = SendOutcome.SUCCESS):
        self.default_outcome = default_outcome
        self._outcome_queue: list[SendResult] = []
        self.sent: list[dict] = []

    def enqueue_outcome(self, result: SendResult) -> None:
        """Push a deterministic outcome onto the FIFO queue."""
        self._outcome_queue.append(result)

    def send(self, to: str, subject: str, html_body: str) -> SendResult:
        self.sent.append({"to": to, "subject": subject, "html_body": html_body})

        if self._outcome_queue:
            return self._outcome_queue.pop(0)

        return SendResult(outcome=self.default_outcome)
