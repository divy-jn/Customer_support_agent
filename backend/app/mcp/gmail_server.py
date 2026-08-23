"""
Gmail MCP Server — provides access to the official Gmail API via FastMCP.

This server exposes tools for agents to send and read emails securely.
Now uses standard SMTP with a Gmail App Password for simplified deployment.
"""

import os
import sys
import smtplib
from email.message import EmailMessage

from mcp.server.fastmcp import FastMCP

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.config import settings

# ──────────────────────────────────────────────
#  MCP Server Setup
# ──────────────────────────────────────────────
mcp = FastMCP(
    "Gmail",
    instructions="MCP server providing access to send and read emails securely.",
)

# ──────────────────────────────────────────────
#  MCP Tools
# ──────────────────────────────────────────────

@mcp.tool()
def send_email(to: str, subject: str, body: str, is_html: bool = True) -> str:
    """
    Send an email via Gmail SMTP.
    
    Args:
        to: The recipient's email address.
        subject: The subject line of the email.
        body: The content of the email (HTML or plain text).
        is_html: True if the body should be parsed as HTML.
    """
    try:
        if not settings.gmail_address or not settings.gmail_app_password:
            return "Failed to send email: GMAIL_ADDRESS or GMAIL_APP_PASSWORD is not set in environment."

        message = EmailMessage()
        
        if is_html:
            message.add_alternative(body, subtype="html")
        else:
            message.set_content(body)
            
        message["To"] = to
        message["From"] = settings.from_email or settings.gmail_address
        message["Subject"] = subject

        # Connect to Gmail SMTP server
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(settings.gmail_address, settings.gmail_app_password)
            server.send_message(message)

        return f"Email successfully sent to {to}."
    
    except Exception as e:
        return f"Failed to send email: {str(e)}"

if __name__ == "__main__":
    mcp.run()
