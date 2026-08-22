import sys
import os
import logging

# Setup logging
logging.basicConfig(level=logging.DEBUG)

# Ensure backend path is in sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app.email_service import (
    send_ticket_created_email,
    send_escalation_email,
    send_resolution_email,
    send_system_alert_email
)

def test_emails():
    print("Testing Email Delivery...")
    target_email = "pdfallen0@gmail.com"
    customer_name = "Test User"
    
    # 1. Ticket Created
    print("\n--- Sending Ticket Created Email ---")
    res1 = send_ticket_created_email(
        customer_email=target_email,
        customer_name=customer_name,
        ticket_id=999,
        subject="Test Ticket",
        description="This is a test description.",
        priority="high"
    )
    print(f"Result: {res1}")

    # 2. Escalation
    print("\n--- Sending Escalation Email ---")
    res2 = send_escalation_email(
        customer_email=target_email,
        customer_name=customer_name,
        session_id="test_session_xyz",
        sentiment="very negative",
        urgency="high",
        last_message="I need a human right now!"
    )
    print(f"Result: {res2}")

    # 3. Resolution
    print("\n--- Sending Resolution Email ---")
    res3 = send_resolution_email(
        customer_email=target_email,
        customer_name=customer_name,
        ticket_id=999,
        subject="Test Ticket",
        resolution="We have successfully resolved the test issue."
    )
    print(f"Result: {res3}")

    # 4. System Alert
    print("\n--- Sending System Alert Email ---")
    res4 = send_system_alert_email(
        alert_type="Database Error",
        message="Connection pool exhausted after 5 retries."
    )
    print(f"Result: {res4}")

if __name__ == "__main__":
    test_emails()
