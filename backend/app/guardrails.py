"""
Guardrails — validates every user input and AI output before processing.

Input Guards:
  - Prompt injection detection (regex + keyword patterns)
  - PII redaction (credit cards, SSN, phone numbers, emails, URLs, IPs)
  - Topic boundary enforcement (reject off-domain requests)
  - Max input length enforcement

Output Guards:
  - Forbidden content filter (no internal tool names, DB IDs, system prompts)
  - Response length cap
  - Hallucination flag (context-grounded check for RAG)
"""

import re
import logging
from dataclasses import dataclass, field

from app.middleware.tracking import record_guardrail_input, record_guardrail_output

logger = logging.getLogger("guardrails")


# ──────────────────────────────────────────────
#  Result Types
# ──────────────────────────────────────────────

@dataclass
class GuardrailResult:
    """Result of a guardrail check."""
    passed: bool
    sanitized_text: str                         # cleaned / redacted version
    violations: list[str] = field(default_factory=list)
    pii_detected: list[str] = field(default_factory=list)
    risk_score: float = 0.0                     # 0.0 = safe, 1.0 = blocked


# ──────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────

MAX_INPUT_LENGTH = 2000
MAX_OUTPUT_LENGTH = 3000

# Prompt injection patterns (case-insensitive)
INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts|rules)", "instruction_override"),
    (r"you\s+are\s+now\s+(a|an|my)", "persona_hijack"),
    (r"(forget|disregard)\s+(everything|all|your)\s+(above|instructions|rules|training)", "instruction_override"),
    (r"system\s*:\s*", "role_injection"),
    (r"<\s*system\s*>", "role_injection"),
    (r"\[\s*system\s*\]", "role_injection"),
    (r"act\s+as\s+(if\s+)?(you\s+are|you're)\s+(a|an|the)", "persona_hijack"),
    (r"pretend\s+(to\s+be|you\s+are|you're)", "persona_hijack"),
    (r"do\s+not\s+follow\s+(your|any|the)\s+(rules|instructions|guidelines)", "instruction_override"),
    (r"reveal\s+(your|the)\s+(system|internal)\s+(prompt|instructions|rules)", "info_extraction"),
    (r"(print|show|display|output|repeat)\s+(your|the)\s+(system|initial)\s+(prompt|message|instructions)", "info_extraction"),
    (r"what\s+(is|are)\s+your\s+(system|initial)\s+(prompt|instructions|rules)", "info_extraction"),
    (r"jailbreak", "jailbreak_keyword"),
    (r"DAN\s+mode", "jailbreak_keyword"),
    (r"developer\s+mode", "jailbreak_keyword"),
]

# PII patterns (expanded)
PII_PATTERNS: dict[str, str] = {
    "credit_card": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "phone_us": r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
    "phone_intl": r"\b\+\d{1,3}[-.\s]?\d{6,14}\b",
    "email": r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b",
    "ip_address": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
}

# Off-topic patterns — things a customer support bot should NOT do
OFF_TOPIC_PATTERNS: list[tuple[str, str]] = [
    (r"(write|generate|create)\s+(me\s+)?(a\s+)?(python|javascript|java|code|script|program)", "code_generation"),
    (r"(tell|give)\s+me\s+a\s+(joke|story|poem|riddle)", "entertainment"),
    (r"(who|what)\s+(is|was)\s+(the\s+)?(president|prime\s+minister|king|queen)\s+of", "trivia"),
    (r"(translate|convert)\s+.+\s+(to|into)\s+(french|spanish|german|hindi|chinese)", "translation"),
    (r"(how\s+to|teach\s+me)\s+(hack|break\s+into|exploit)", "malicious_request"),
    (r"(recipe|cook|bake)\s+(for|a)\s+", "unrelated"),
]

# Forbidden content in AI responses (must not leak)
FORBIDDEN_OUTPUT_PATTERNS: list[tuple[str, str]] = [
    (r"(lookup_customer|get_customer_history|create_ticket|cancel_order|process_refund|check_inventory|track_order|update_ticket|web_search|get_ticket)", "leaked_tool_name"),
    (r"supabase", "leaked_infrastructure"),
    (r"pinecone", "leaked_infrastructure"),
    (r"langchain|langgraph|langsmith", "leaked_infrastructure"),
    (r"redis|upstash", "leaked_infrastructure"),
    (r"system\s*prompt", "leaked_system_prompt"),
    (r"you\s+are\s+a\s+(customer\s+support|helpful)\s+(agent|assistant|system)\s+with\s+access", "leaked_system_prompt"),
    (r"postgresql|asyncpg|psycopg", "leaked_infrastructure"),
    (r"api_key|secret_key|password", "leaked_secret"),
]


# ──────────────────────────────────────────────
#  Input Guardrails
# ──────────────────────────────────────────────

def validate_input(message: str) -> GuardrailResult:
    """
    Validate a customer's input message.
    Returns a GuardrailResult with sanitized text and any violations.
    """
    violations = []
    pii_detected = []
    risk_score = 0.0
    sanitized = message

    # 1. Length check
    if len(message) > MAX_INPUT_LENGTH:
        sanitized = message[:MAX_INPUT_LENGTH]
        violations.append(f"input_too_long:{len(message)}")
        risk_score += 0.1

    # 2. Empty message
    if not message.strip():
        result = GuardrailResult(
            passed=False,
            sanitized_text="",
            violations=["empty_message"],
            risk_score=1.0,
        )
        record_guardrail_input(blocked=True, violations=["empty_message"])
        return result

    # 3. Prompt injection detection
    for pattern, label in INJECTION_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            violations.append(f"prompt_injection:{label}")
            risk_score += 0.5
            logger.warning(
                "Prompt injection detected",
                extra={"pattern": label, "message_preview": message[:100]},
            )

    # 4. PII detection & redaction
    for pii_type, pattern in PII_PATTERNS.items():
        matches = re.findall(pattern, sanitized)
        if matches:
            pii_detected.append(f"{pii_type}:{len(matches)}")
            # Redact PII from the text we log / store (but keep it for the LLM to process)
            for match in matches:
                if pii_type == "email":
                    # Redact email: keep first 2 chars + domain
                    parts = match.split("@")
                    redacted = parts[0][:2] + "***@" + parts[1] if len(parts) == 2 else "***@***"
                elif pii_type == "ip_address":
                    redacted = match.split(".")[0] + ".*.*.*"
                else:
                    redacted = match[:4] + re.sub(r'\d', '*', match[4:])
                sanitized = sanitized.replace(match, redacted)
            logger.info(
                "PII detected and redacted for logging",
                extra={"pii_type": pii_type, "count": len(matches)},
            )

    # 5. Topic boundary check
    for pattern, label in OFF_TOPIC_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            violations.append(f"off_topic:{label}")
            risk_score += 0.3

    # Determine pass/fail
    # Block if risk_score >= 0.5 (i.e., prompt injection or multiple off-topic signals)
    passed = risk_score < 0.5

    if not passed:
        logger.warning(
            "Input blocked by guardrails",
            extra={
                "violations": violations,
                "risk_score": risk_score,
                "message_preview": message[:80],
            },
        )

    # Record metrics
    record_guardrail_input(blocked=not passed, violations=violations)

    return GuardrailResult(
        passed=passed,
        sanitized_text=sanitized,
        violations=violations,
        pii_detected=pii_detected,
        risk_score=round(risk_score, 2),
    )


# ──────────────────────────────────────────────
#  Output Guardrails
# ──────────────────────────────────────────────

def validate_output(
    response: str,
    retrieved_context: str | None = None,
) -> GuardrailResult:
    """
    Validate the AI's response before sending it to the customer.

    Args:
        response: The AI-generated response text.
        retrieved_context: Optional RAG context to check grounding against.
    """
    violations = []
    risk_score = 0.0
    sanitized = response

    # 1. Length cap
    if len(response) > MAX_OUTPUT_LENGTH:
        sanitized = response[:MAX_OUTPUT_LENGTH] + "\n\n[Response truncated]"
        violations.append(f"output_too_long:{len(response)}")
        risk_score += 0.1

    # 2. Forbidden content filter (leaked internals)
    for pattern, label in FORBIDDEN_OUTPUT_PATTERNS:
        match = re.search(pattern, response, re.IGNORECASE)
        if match:
            violations.append(f"forbidden_content:{label}")
            risk_score += 0.3
            # Redact the forbidden content from the response
            sanitized = re.sub(pattern, "[redacted]", sanitized, flags=re.IGNORECASE)
            logger.warning(
                "Forbidden content detected in AI output",
                extra={"label": label, "matched": match.group()[:50]},
            )

    # 3. Hallucination flag (simple heuristic for RAG responses)
    if retrieved_context and "No relevant information" not in retrieved_context:
        hallucination_indicators = _check_hallucination(sanitized, retrieved_context)
        if hallucination_indicators:
            violations.extend(hallucination_indicators)
            risk_score += 0.2

    passed = risk_score < 0.5

    # Record metrics
    record_guardrail_output(blocked=not passed, violations=violations)

    return GuardrailResult(
        passed=passed,
        sanitized_text=sanitized,
        violations=violations,
        risk_score=round(risk_score, 2),
    )


def _check_hallucination(response: str, context: str) -> list[str]:
    """
    Simple heuristic hallucination detection.
    Flags responses that contain specific claims (numbers, dates, percentages)
    not present in the retrieved context.
    """
    flags = []

    # Extract specific numeric claims from the response
    response_numbers = set(re.findall(r'\b\d+(?:\.\d+)?%?\b', response))
    context_numbers = set(re.findall(r'\b\d+(?:\.\d+)?%?\b', context))

    # Numbers in response but not in context could be hallucinated
    suspicious_numbers = response_numbers - context_numbers
    # Filter out common non-suspicious numbers (single digits, years, etc.)
    suspicious_numbers = {
        n for n in suspicious_numbers
        if len(n) > 1 and not (1900 <= int(re.sub(r'%', '', n).split('.')[0]) <= 2030 if re.sub(r'%', '', n).split('.')[0].isdigit() else False)
    }

    if len(suspicious_numbers) > 2:
        flags.append(f"potential_hallucination:ungrounded_numbers:{','.join(list(suspicious_numbers)[:5])}")

    return flags


# ──────────────────────────────────────────────
#  Rejection Messages
# ──────────────────────────────────────────────

def get_rejection_message(result: GuardrailResult) -> str:
    """Generate a user-friendly rejection message based on the violations."""
    violations = result.violations

    if any("prompt_injection" in v for v in violations):
        return (
            "I'm sorry, but I can only help with customer support questions "
            "related to your orders, products, billing, and account. "
            "How can I assist you with those today?"
        )

    if any("off_topic" in v for v in violations):
        labels = [v.split(":")[1] for v in violations if "off_topic" in v]
        if "malicious_request" in labels:
            return "I'm unable to assist with that type of request. Is there anything else I can help you with regarding your account or orders?"
        return (
            "That's a bit outside my area! I'm a customer support assistant — "
            "I can help with orders, returns, shipping, billing, and product questions. "
            "What can I help you with?"
        )

    if any("empty_message" in v for v in violations):
        return "It looks like your message was empty. Could you please rephrase your question?"

    return (
        "I wasn't able to process that request. Could you please rephrase your question? "
        "I'm here to help with orders, shipping, returns, billing, and product inquiries."
    )
