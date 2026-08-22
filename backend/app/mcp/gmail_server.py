"""
Gmail MCP Server — provides access to the official Gmail API via FastMCP.

This server exposes tools for agents to send and read emails securely.
Requires credentials.json to be placed in the backend root directory.
"""

import os
import sys
import base64
from email.message import EmailMessage

from mcp.server.fastmcp import FastMCP
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.config import settings

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CREDENTIALS_PATH = os.path.join(BACKEND_DIR, "credentials.json")
TOKEN_PATH = os.path.join(BACKEND_DIR, "token.json")

# ──────────────────────────────────────────────
#  MCP Server Setup
# ──────────────────────────────────────────────
mcp = FastMCP(
    "Gmail",
    instructions="MCP server providing access to send and read emails via Gmail API.",
)

def _get_gmail_service():
    """Authenticate and return the Gmail API service."""
    creds = None
    # 1. Check for token in environment variable first (for Hugging Face Spaces)
    import json
    if "GMAIL_TOKEN_JSON" in os.environ:
        try:
            token_data = json.loads(os.environ["GMAIL_TOKEN_JSON"])
            creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        except Exception as e:
            print(f"Failed to parse GMAIL_TOKEN_JSON: {e}")

    # 2. The file token.json stores the user's access and refresh tokens locally.
    if not creds and os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(
                    f"credentials.json not found at {CREDENTIALS_PATH}. "
                    "Please download OAuth client ID credentials from Google Cloud Console."
                )
            # Run local server flow to authenticate
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=8081, host="127.0.0.1")
            
        # Save the credentials for the next run
        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)

# ──────────────────────────────────────────────
#  MCP Tools
# ──────────────────────────────────────────────

@mcp.tool()
def send_email(to: str, subject: str, body: str, is_html: bool = True) -> str:
    """
    Send an email via the Gmail API.
    
    Args:
        to: The recipient's email address.
        subject: The subject line of the email.
        body: The content of the email (HTML or plain text).
        is_html: True if the body should be parsed as HTML.
    """
    try:
        service = _get_gmail_service()
        
        message = EmailMessage()
        
        if is_html:
            message.add_alternative(body, subtype="html")
        else:
            message.set_content(body)
            
        message["To"] = to
        message["From"] = "me"
        message["Subject"] = subject

        # Encoded message
        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        create_message = {"raw": encoded_message}

        send_message = (
            service.users()
            .messages()
            .send(userId="me", body=create_message)
            .execute()
        )
        return f"Email successfully sent to {to}. Message Id: {send_message['id']}"
    
    except Exception as e:
        return f"Failed to send email: {str(e)}"


if __name__ == "__main__":
    mcp.run()
