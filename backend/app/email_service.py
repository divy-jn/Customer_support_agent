"""
Email Notification Service — sends emails via Gmail OAuth API.

Provides templated email notifications for:
- Ticket creation confirmation (to customer)
- Escalation alerts (to support team + customer)
- Ticket resolution (to customer)
- System alerts (to tech team)
"""

import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  HTML Email Templates
# ──────────────────────────────────────────────

def _base_template(title: str, body: str) -> str:
    """Wrap email content in a styled HTML template."""
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0a0a0f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:600px;margin:0 auto;padding:32px 24px;">
  <div style="text-align:center;margin-bottom:32px;">
    <h1 style="color:#818cf8;font-size:24px;margin:0;">Project Bestie</h1>
    <p style="color:#606078;font-size:13px;margin:4px 0 0;">Intelligent Customer Support</p>
  </div>
  <div style="background:#16161f;border:1px solid #2a2a3a;border-radius:16px;padding:32px;">
    <h2 style="color:#f0f0f5;font-size:20px;margin:0 0 16px;">{title}</h2>
    {body}
  </div>
  <div style="text-align:center;margin-top:24px;">
    <p style="color:#606078;font-size:11px;margin:0;">
      Powered by Project Bestie &bull; LangGraph + MCP + RAG
    </p>
  </div>
</div>
</body>
</html>"""


def _info_row(label: str, value: str) -> str:
    return f"""<tr>
      <td style="color:#9090a8;font-size:14px;padding:8px 12px;border-bottom:1px solid #2a2a3a;">{label}</td>
      <td style="color:#f0f0f5;font-size:14px;padding:8px 12px;border-bottom:1px solid #2a2a3a;font-weight:500;">{value}</td>
    </tr>"""


def _info_table(rows: list[tuple[str, str]]) -> str:
    inner = "".join(_info_row(label, val) for label, val in rows)
    return f'<table style="width:100%;border-collapse:collapse;margin:16px 0;">{inner}</table>'


# ──────────────────────────────────────────────
#  Send via Gmail MCP API
# ──────────────────────────────────────────────

def _send_email(to: str | list[str], subject: str, html: str) -> dict:
    """Send an email via the local Gmail API. Returns result dict."""
    from app.mcp.gmail_server import send_email
    
    if isinstance(to, list):
        to = ", ".join(to)
        
    try:
        # Call the tool directly in-process
        result_msg = send_email(to=to, subject=subject, body=html, is_html=True)
        if isinstance(result_msg, str) and result_msg.startswith("Failed"):
            logger.error(f"Gmail send failed: {result_msg}")
            return {"status": "error", "error": result_msg}
        logger.info(f"Gmail sent: {result_msg}")
        return {"status": "sent", "message": result_msg}
    except Exception as e:
        logger.error("Failed to send Gmail: %s — %s", subject, str(e))
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────
#  Public Email Functions
# ──────────────────────────────────────────────

def send_ticket_created_email(
    customer_email: str,
    customer_name: str,
    ticket_id: int,
    subject: str,
    description: str,
    priority: str = "medium",
) -> dict:
    """Send ticket creation confirmation to the customer."""
    body = f"""
    <p style="color:#9090a8;font-size:14px;line-height:1.6;">
      Hi <strong style="color:#f0f0f5;">{customer_name}</strong>, your support ticket has been created.
      Our team will review it shortly.
    </p>
    {_info_table([
        ("Ticket ID", f"#{ticket_id}"),
        ("Subject", subject),
        ("Priority", f'<span style="text-transform:capitalize;">{priority}</span>'),
        ("Status", "Open"),
    ])}
    <p style="color:#606078;font-size:13px;margin-top:16px;">
      <em>Description:</em> {description[:200]}{'...' if len(description) > 200 else ''}
    </p>
    """
    html = _base_template("Ticket Created", body)
    return _send_email(customer_email, f"[Project Bestie] Ticket #{ticket_id} Created — {subject}", html)


def send_escalation_email(
    customer_email: str | None,
    customer_name: str,
    session_id: str,
    sentiment: str,
    urgency: str,
    last_message: str,
) -> dict:
    """Send escalation alert to the support team (and optionally the customer)."""
    # Support team notification
    team_body = f"""
    <p style="color:#f87171;font-size:14px;font-weight:600;">⚠️ Escalation Alert</p>
    <p style="color:#9090a8;font-size:14px;line-height:1.6;">
      A customer conversation has been escalated and requires human attention.
    </p>
    {_info_table([
        ("Customer", customer_name or "Unknown"),
        ("Session", session_id[:12] + "..."),
        ("Sentiment", f'<span style="color:#f87171;">{sentiment}</span>'),
        ("Urgency", f'<span style="color:#fbbf24;text-transform:uppercase;">{urgency}</span>'),
    ])}
    <p style="color:#9090a8;font-size:13px;margin-top:16px;">
      <strong>Last message:</strong> "{last_message[:300]}"
    </p>
    <p style="color:#818cf8;font-size:14px;margin-top:20px;">
      → Open the <strong>Agent Dashboard</strong> to take over this conversation.
    </p>
    """
    html = _base_template("Escalation Alert", team_body)
    result = _send_email(settings.support_team_email, "[Project Bestie] 🚨 Escalation Alert — Immediate Action Required", html)

    # Customer notification (optional)
    if customer_email:
        cust_body = f"""
        <p style="color:#9090a8;font-size:14px;line-height:1.6;">
          Hi <strong style="color:#f0f0f5;">{customer_name}</strong>, we understand your concern.
          A human support agent has been notified and will be with you shortly.
        </p>
        <p style="color:#606078;font-size:13px;margin-top:16px;">
          You can continue chatting — your conversation will be handed to a live agent.
        </p>
        """
        cust_html = _base_template("We're Connecting You With a Human Agent", cust_body)
        _send_email(customer_email, "[Project Bestie] A human agent is on the way", cust_html)

    return result


def send_resolution_email(
    customer_email: str,
    customer_name: str,
    ticket_id: int,
    subject: str,
    resolution: str,
) -> dict:
    """Send ticket resolution notification to the customer."""
    body = f"""
    <p style="color:#9090a8;font-size:14px;line-height:1.6;">
      Hi <strong style="color:#f0f0f5;">{customer_name}</strong>, your support ticket has been resolved.
    </p>
    {_info_table([
        ("Ticket ID", f"#{ticket_id}"),
        ("Subject", subject),
        ("Status", '<span style="color:#4ade80;">Closed</span>'),
    ])}
    <p style="color:#9090a8;font-size:14px;margin-top:16px;">
      <strong>Resolution:</strong> {resolution or 'Your issue has been addressed.'}
    </p>
    <p style="color:#606078;font-size:13px;margin-top:20px;">
      If you need further assistance, don't hesitate to reach out again.
    </p>
    """
    html = _base_template("Ticket Resolved", body)
    return _send_email(customer_email, f"[Project Bestie] Ticket #{ticket_id} Resolved", html)


def send_system_alert_email(
    alert_type: str,
    message: str,
    details: dict | None = None,
) -> dict:
    """Send system alert to the tech team."""
    details_html = ""
    if details:
        rows = [(k, str(v)) for k, v in details.items()]
        details_html = _info_table(rows)

    color = "#f87171" if alert_type == "error" else "#fbbf24"
    body = f"""
    <p style="color:{color};font-size:14px;font-weight:600;">
      🔧 System Alert — {alert_type.upper()}
    </p>
    <p style="color:#9090a8;font-size:14px;line-height:1.6;">
      {message}
    </p>
    {details_html}
    <p style="color:#606078;font-size:12px;margin-top:16px;">
      Timestamp: {datetime.utcnow().isoformat()}Z
    </p>
    """
    html = _base_template(f"System Alert — {alert_type.upper()}", body)
    return _send_email(settings.tech_team_email, f"[Project Bestie] System Alert: {alert_type}", html)
