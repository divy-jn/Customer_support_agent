import pytest
from unittest.mock import patch, MagicMock
from app.config import settings
from app.mcp.gmail_server import send_email
from app.email_service import (
    send_ticket_created_email,
    send_escalation_email,
    send_resolution_email,
    send_system_alert_email,
    send_custom_ticket_email,
    send_order_update_email
)

@pytest.fixture
def mock_smtp():
    with patch("smtplib.SMTP_SSL") as mock:
        instance = MagicMock()
        mock.return_value.__enter__.return_value = instance
        yield instance

@pytest.fixture(autouse=True)
def setup_config(monkeypatch):
    monkeypatch.setattr(settings, "app_environment", "development")
    monkeypatch.setattr(settings, "gmail_address", "sender@example.com")
    monkeypatch.setattr(settings, "gmail_app_password", "fake_password")
    monkeypatch.setattr(settings, "from_email", "sender@example.com")
    monkeypatch.setattr(settings, "email_test_recipient", "test-inbox@example.com")

@pytest.mark.parametrize(("environment", "expected"), [
    ("development", "test-inbox@example.com"),
    ("test", "test-inbox@example.com"),
    ("staging", "realcustomer@example.com"),
    ("production", "realcustomer@example.com"),
])
def test_central_routing_override(mock_smtp, monkeypatch, environment, expected):
    monkeypatch.setattr(settings, "app_environment", environment)
    send_email(to="realcustomer@example.com", subject="Test", body="Body")
    
    mock_smtp.send_message.assert_called_once()
    msg = mock_smtp.send_message.call_args[0][0]
    
    assert msg["To"] == expected
    assert msg["From"] == "sender@example.com"
    assert msg["Subject"] == "Test"

def test_central_routing_disabled(mock_smtp, monkeypatch):
    # Test that it falls back to normal recipient if test override is missing
    monkeypatch.setattr(settings, "email_test_recipient", None)
    monkeypatch.setattr(settings, "from_email", "")
    
    send_email(to="realcustomer@example.com", subject="Test", body="Body")
    
    mock_smtp.send_message.assert_called_once()
    msg = mock_smtp.send_message.call_args[0][0]
    assert msg["To"] == "realcustomer@example.com"
    assert msg["From"] == "sender@example.com"

def test_all_helpers_use_centralized_path(mock_smtp):
    # Verify every email helper uses the centralized mail path
    send_ticket_created_email("c1@example.com", "Cust 1", 123, "Subj", "Desc")
    send_escalation_email("c2@example.com", "Cust 2", "sess1", "bad", "high", "msg")
    send_resolution_email("c3@example.com", "Cust 3", 123, "Subj", "Resolved")
    send_system_alert_email("error", "Failed")
    send_custom_ticket_email("c4@example.com", "Cust 4", 123, "Subj", "Content")
    send_order_update_email("c5@example.com", "Cust 5", 456, "shipped", "Msg")
    
    # 6 helpers were called, but send_escalation_email sends TWO emails (team + customer)
    assert mock_smtp.send_message.call_count == 7
    
    # All 7 emails must have gone to the test recipient
    for call in mock_smtp.send_message.call_args_list:
        msg = call[0][0]
        assert msg["To"] == "test-inbox@example.com"
        assert msg["From"] == "sender@example.com"
        # Content includes template elements
        assert "Support Team" in str(msg) or "Project Bestie" not in str(msg)
